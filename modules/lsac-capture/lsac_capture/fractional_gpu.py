"""Batched fractional-band covariance kernels. No device capture or playback.

Segments split at every spatial-cell boundary and every orientation subframe.
Padding for batched matrix multiplication is zero, not additional observations.
FP32/complex64 device work, FP64 host accumulation; no FP16/INT8 conversion.
"""
import math
import numpy as np


def band_grid(divisions=12):
    edges=125/2**.5 * 2**(np.arange(8*divisions+1)/divisions)
    return np.sqrt(edges[:-1]*edges[1:]),np.column_stack([edges[:-1],edges[1:]])


def band_bin_limits(bands,n,rate):
    # Half-open bins. Snap only numerical roundoff at exact integer bin edges.
    # Both CPU and GPU use these integer limits, not FP32 frequency comparisons.
    return np.ceil(np.asarray(bands)*n/rate-1e-9).astype(np.int64)


def fft_length(length,rate=48000):
    """Keep tails on smooth FFT sizes; avoid expensive large-prime Bluestein plans."""
    return length if length<rate else int(math.ceil(length/rate)*rate)


def segments(cell_ids,block_frames=192):
    ids=np.asarray(cell_ids);n=len(ids)
    boundaries=np.r_[0,np.flatnonzero((ids[1:]!=ids[:-1])|((np.arange(1,n)%block_frames)==0))+1,n]
    a=boundaries[:-1];b=boundaries[1:];keep=ids[a]>=0
    a=a[keep];b=b[keep]
    assert np.sum(b-a)==np.sum(ids>=0)
    return a,b,ids[a]


class BandKernel:
    def __init__(self,device='xpu',batch=8):
        import torch
        self.torch=torch;self.device=torch.device('xpu:0' if device=='xpu' else device);self.batch=batch
        if device=='xpu' and not torch.xpu.is_available():raise RuntimeError('Intel XPU unavailable; CPU fallback refused')
        self.last_device=None

    def sync(self):
        if self.device.type=='xpu':self.torch.xpu.synchronize()

    def spectrum(self,x):
        t=self.torch
        data=t.as_tensor(np.ascontiguousarray(x.T,dtype=np.float32),device=self.device)
        spec=t.fft.fft(data,n=fft_length(len(x)),dim=-1)
        assert spec.device==self.device
        return spec

    def integrate(self,spec,rate,bands,offset,frames,cell_ids,rotation,already_world=False):
        """Yield band batches of SUM covariance in normalized digital units."""
        t=self.torch;n=spec.shape[-1];a,b,ids=segments(cell_ids)
        cells=int(np.max(cell_ids))+1 if np.any(cell_ids>=0) else 0
        if not len(a):
            for start in range(0,len(bands),self.batch):yield start,np.zeros((min(self.batch,len(bands)-start),cells,4,4),complex)
            return
        lengths=b-a;assert max(lengths)<=192
        gather=a[:,None]+np.arange(192)[None,:]
        mask=(gather<b[:,None]).astype(np.float32);gather=np.minimum(gather,frames-1)
        gather=t.as_tensor(gather,device=self.device);mask=t.as_tensor(mask,device=self.device)
        identifiers=t.as_tensor(ids,dtype=t.int64,device=self.device)
        u=np.zeros((len(a),4,4),np.complex64);u[:,0,0]=1;u[:,1:,1:]=rotation[a//192]
        u=t.as_tensor(u,device=self.device)
        fft_bins=t.arange(n,device=self.device)
        limits=band_bin_limits(bands,n,rate)
        with t.inference_mode():
            for start in range(0,len(bands),self.batch):
                edges=t.as_tensor(limits[start:start+self.batch],device=self.device)
                take=(fft_bins[None,:]>=edges[:,0:1])&(fft_bins[None,:]<edges[:,1:2])
                y=t.fft.ifft(spec[None,:,:]*(2*take[:,None,:]),dim=-1)[:,:,offset:offset+frames]
                # AmbiX assumed WYZX -> normalized Cartesian WXYZ.
                if not already_world:y=y[:,[0,3,1,2],:]
                z=y[:,:,gather].permute(0,2,1,3)*mask[None,:,None,:]
                local=.5*(z@z.conj().transpose(-2,-1))
                physical=local if already_world else u[None,:,:,:]@local@u.transpose(-2,-1)[None,:,:,:]
                result=t.zeros((len(edges),cells,32),dtype=t.float32,device=self.device)
                result.index_add_(1,identifiers,t.view_as_real(physical.resolve_conj()).reshape(len(edges),len(a),32))
                assert result.device==self.device
                self.last_device=str(result.device)
                values=result.cpu().numpy().reshape(len(edges),cells,4,4,2)
                yield start,values[...,0].astype(float)+1j*values[...,1].astype(float)


def numpy_reference(x,rate,bands,offset,frames,cell_ids,rotation,already_world=False):
    """Independent FP64 FFT/per-frame rotation/direct cell sums for validation."""
    n=fft_length(len(x),rate)
    spec=np.fft.fft(x.T,n=n,axis=-1);fft_bins=np.arange(n);limits=band_bin_limits(bands,n,rate)
    result=np.zeros((len(bands),int(np.max(cell_ids))+1,4,4),complex)
    selected=cell_ids>=0;ids=cell_ids[selected]
    r=rotation[np.arange(frames)//192]
    for index,edges in enumerate(bands):
        mask=(fft_bins>=limits[index,0])&(fft_bins<limits[index,1])
        y=np.fft.ifft(spec*(2*mask),axis=-1)[:,offset:offset+frames].T
        if already_world:b=y
        else:
            b=np.empty_like(y);b[:,0]=y[:,0];b[:,1:]=np.einsum('nij,nj->ni',r,y[:,[3,1,2]])
        b=b[selected]
        for i in range(4):
            for j in range(4):
                v=.5*b[:,i]*b[:,j].conj()
                result[index,:,i,j]=np.bincount(ids,weights=v.real,minlength=result.shape[1])+1j*np.bincount(ids,weights=v.imag,minlength=result.shape[1])
    return result
