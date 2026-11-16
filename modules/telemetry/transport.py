"""Bounded authenticated-loopback fragment reassembly; never used on audio threads."""
from collections import OrderedDict
import struct
import time

FRAGMENT=struct.Struct('<4sQHH')
CHUNK=48000
MAX_BLOCK=32+160*1968


def fragments(raw, ident):
    count=(len(raw)+CHUNK-1)//CHUNK
    for index in range(count):
        yield FRAGMENT.pack(b'NSPF',ident,index,count)+raw[index*CHUNK:(index+1)*CHUNK]


class Reassembler:
    def __init__(self,clock=time.monotonic):
        self.frames=OrderedDict();self.clock=clock;self.expired=0
    def expire(self):
        now=self.clock()
        for key,frame in list(self.frames.items()):
            if now-frame['at']>.2:
                del self.frames[key];self.expired+=1
    def add(self,data):
        self.expire()
        if len(data)<FRAGMENT.size:return None
        magic,ident,index,count=FRAGMENT.unpack_from(data)
        payload=data[FRAGMENT.size:]
        if magic!=b'NSPF' or not 1<=count<=7 or index>=count or not 0<len(payload)<=CHUNK:return None
        if index<count-1 and len(payload)!=CHUNK:return None
        if ident not in self.frames:
            if len(self.frames)>=16:self.frames.popitem(last=False);self.expired+=1
            self.frames[ident]=dict(at=self.clock(),count=count,parts={})
        frame=self.frames[ident]
        if frame['count']!=count:return None
        frame['parts'][index]=payload
        if len(frame['parts'])==count:
            raw=b''.join(frame['parts'][i] for i in range(count));del self.frames[ident]
            return raw if len(raw)<=MAX_BLOCK else None
        return None
