import unittest
import numpy as np

from lsac_capture.multisource_model import reflect_points,transfer_operator,prediction,fit_covariances,relative_error


class MultisourceTests(unittest.TestCase):
    def test_reflection_flips_signed_plane_distance_and_is_reversible(self):
        points=np.array([[.1,.2,.3],[-.1,.4,.2]])
        reflected=reflect_points(points,[0,1,0],-.03)
        np.testing.assert_allclose(reflected[:,1],-.06-points[:,1])
        np.testing.assert_allclose(reflect_points(reflected,[0,1,0],-.03),points)

    def test_coherent_cross_terms_can_cancel_where_power_sum_cannot(self):
        h=transfer_operator([[0,.2,.4]],[[0,0,0],[0,0,0]],[500],[0,1,0],-.03,0.)
        q=np.array([1,-1]);s=np.outer(q,q.conj())[None,:,:]
        np.testing.assert_allclose(prediction(h,s),0,atol=1e-14)
        self.assertGreater(float(prediction(h,np.eye(2)[None,:,:])[0,0,0].real),0)

    def test_unknown_complex_coupling_is_fitted_and_predicts_heldout_receivers(self):
        rng=np.random.default_rng(310)
        h=rng.normal(size=(16,1,4,2))+1j*rng.normal(size=(16,1,4,2))
        q=np.array([1,.7j]);truth=np.outer(q,q.conj())[None,:,:];c=prediction(h,truth)
        fitted,_=fit_covariances(h[:12],c[:12],iterations=200,ridge=0)
        diagonal,_=fit_covariances(h[:12],c[:12],iterations=200,ridge=0,diagonal_only=True)
        full_error=relative_error(prediction(h[12:],fitted),c[12:],[1]*4)
        diagonal_error=relative_error(prediction(h[12:],diagonal),c[12:],[1]*4)
        self.assertLess(full_error,.001)
        self.assertGreater(diagonal_error,.1)
        self.assertGreaterEqual(np.linalg.eigvalsh(fitted).min(),-1e-10)

    def test_receiver_basis_rotates_without_imposing_a_cardioid_source(self):
        p=np.array([[.2,.2,.4],[-.3,.15,.5]]);s=np.array([[.1,0,.1],[-.1,0,.1]])
        r=np.array([[0,-1,0],[1,0,0],[0,0,1]])
        a=transfer_operator(p,s,[450,550],[0,1,0],-.03,.7)
        b=transfer_operator(p@r.T,s@r.T,[450,550],r@[0,1,0],-.03,.7)
        np.testing.assert_allclose(a[:,:,0,:],b[:,:,0,:],atol=1e-12)
        np.testing.assert_allclose(np.einsum('ab,nfbk->nfak',r,a[:,:,1:,:]),b[:,:,1:,:],atol=1e-12)

    def test_reflected_pressure_is_combined_before_covariance(self):
        p=[[.2,.2,.4]];s=[[.1,0,.1]]
        direct=transfer_operator(p,s,[500],[0,1,0],-.03,0.)
        both=transfer_operator(p,s,[500],[0,1,0],-.03,1.)
        mirror=both-direct;one=np.ones((1,1,1))
        coherent=prediction(both,one);incoherent=prediction(direct,one)+prediction(mirror,one)
        self.assertGreater(float(np.linalg.norm(coherent-incoherent)),.001)
