"""Trial screen-centred scan window, matching the reviewed side-view shape.

All numeric proportions are explicit trial settings. Heights use the measured
palm plane; input coordinates are palm-aligned with screen-centre origin.
"""
import math
import numpy as np
from numpy.polynomial import Polynomial as Poly


TRIAL_WINDOW = dict(kind='reviewed_side_profile_trial',
    upper_slope=.55, upper_knee_z_m=.65, upper_transition_m=.25,
    lower_initial_slope=.50, lower_transition_m=.90,
    horizontal_total_degrees=[130.,110.,90.],
    status='shape_reviewed_numeric_dimensions_trial_not_final')


class ScanWindow:
    def __init__(self, geometry, settings):
        self.settings = dict(TRIAL_WINDOW, **settings)
        self.geometry = geometry
        self.height = geometry['screen_center_height_m']
        bottom = geometry['screen_bottom_render_endpoints_m'][0]
        self.upper_start = [-bottom[2], 2*self.height-bottom[1]]
        self.lower_start = [geometry['base_hinge_render_m'][2]+geometry['schematic_base_size_m'][2], 0.]
        self.normal = np.array([sum(a*b for a,b in zip(axis,geometry['screen_normal_world']))
                               for axis in geometry['basis_world_columns']])
        s = self.settings
        for key in ('upper_slope','upper_transition_m','lower_initial_slope','lower_transition_m'):
            if not math.isfinite(s[key]) or s[key] <= 0:
                raise ValueError('Positive finite window slope/transition required')
        if not math.isfinite(s['upper_knee_z_m']) or s['upper_knee_z_m'] < self.upper_start[0]:
            raise ValueError('Upper knee must be ahead of screen edge')
        if len(s['horizontal_total_degrees']) != 3 or any(not math.isfinite(x) or not 0 < x < 180 for x in s['horizontal_total_degrees']):
            raise ValueError('Three horizontal total angles in (0,180) required')

    @staticmethod
    def profile(z, start, slope, knee, width, clamp_before=False):
        if clamp_before and z <= start[0]:
            return start[1]
        if z <= knee:
            return start[1]+slope*(z-start[0])
        t = min(1.,(z-knee)/width)
        return start[1]+slope*(knee-start[0])+slope*width*(t-t**3+.5*t**4)

    def limits(self, z):
        s = self.settings
        return [self.profile(z,self.lower_start,s['lower_initial_slope'],self.lower_start[0],s['lower_transition_m'],True),
                self.profile(z,self.upper_start,s['upper_slope'],s['upper_knee_z_m'],s['upper_transition_m'])]

    def evaluate(self, point, layer):
        x,y,z = point
        height = y+self.height
        low,high = self.limits(z)
        forward = float(self.normal @ np.array(point))
        lateral_limit = max(0.,forward)*math.tan(math.radians(self.settings['horizontal_total_degrees'][layer]/2))
        if forward <= 0 or z < self.upper_start[0]:
            reason = 'behind_screen'
        elif height < low:
            reason = 'too_low'
        elif height > high:
            reason = 'too_high'
        elif abs(x) > lateral_limit:
            reason = 'too_far_sideways'
        else:
            reason = 'inside'
        return dict(inside=reason=='inside',reason=reason,height_above_palm_m=height,
                    height_limits_m=[low,high],lateral_limit_m=lateral_limit)

    def contains(self, point, layer):
        return self.evaluate(point,layer)['inside']

    def cuts(self, a, b):
        """Polynomial roots clip segments at both height profiles exactly.

        Radial layer/grid crossings are handled by the trajectory accumulator.
        Horizontal planes for all three layer angles can safely be extra cuts.
        """
        a=np.array(a);delta=np.array(b)-a;z=Poly([a[2],delta[2]])
        h=Poly([a[1]+self.height,delta[1]])
        s=self.settings
        cuts={0.,1.}
        breaks=[self.upper_start[0],s['upper_knee_z_m'],s['upper_knee_z_m']+s['upper_transition_m'],
                self.lower_start[0],self.lower_start[0]+s['lower_transition_m']]
        if abs(delta[2])>1e-12:
            cuts.update(t for value in breaks if 0 < (t:=(value-a[2])/delta[2]) < 1)
        partitions=sorted(cuts)
        def polynomial(start,slope,knee,width,mid,clamp=False):
            if clamp and mid<=start[0]:return Poly([start[1]])
            if mid<=knee:return start[1]+slope*(z-start[0])
            if mid>=knee+width:return Poly([start[1]+slope*(knee-start[0])+slope*width*.5])
            u=(z-knee)/width
            return start[1]+slope*(knee-start[0])+slope*width*(u-u**3+.5*u**4)
        for lo,hi in zip(partitions,partitions[1:]):
            mid=float(z((lo+hi)/2))
            top=polynomial(self.upper_start,s['upper_slope'],s['upper_knee_z_m'],s['upper_transition_m'],mid)
            bottom=polynomial(self.lower_start,s['lower_initial_slope'],self.lower_start[0],s['lower_transition_m'],mid,True)
            for equation in (h-top,h-bottom):
                for root in equation.roots():
                    if abs(complex(root).imag)<1e-8 and lo < float(complex(root).real) < hi:
                        cuts.add(float(complex(root).real))
        forward0=float(self.normal@a);forward_delta=float(self.normal@delta)
        for angle in s['horizontal_total_degrees']:
            tangent=math.tan(math.radians(angle/2))
            for sign in (-1,1):
                intercept=sign*a[0]-tangent*forward0
                gradient=sign*delta[0]-tangent*forward_delta
                if abs(gradient)>1e-12:
                    t=-intercept/gradient
                    if 0<t<1:cuts.add(t)
        if abs(forward_delta)>1e-12:
            t=-forward0/forward_delta
            if 0<t<1:cuts.add(t)
        return cuts

    def snapshot(self):
        samples=[]
        for z in np.linspace(self.upper_start[0],1.25,100):
            low,high=self.limits(float(z));samples.append([float(z),low,high])
        return dict(**self.settings,screen_centre_height_m=self.height,
                    normal_palm=self.normal.tolist(),
                    upper_start_palm_m=self.upper_start,lower_start_palm_m=self.lower_start,
                    side_profile=samples,horizontal_reference='total_angle_about_measured_screen_normal')
