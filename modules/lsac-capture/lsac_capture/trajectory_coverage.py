"""Metric trajectory coverage; angular projections never decide occupancy.

Straight segments between continuous pose callbacks are clipped exactly at
Cartesian grid planes and radial layer boundaries. Grid size is a configurable
navigation scale, not tracking accuracy or an acoustic sampling requirement.
"""
from collections import defaultdict
import math

import numpy as np


class TrajectoryCoverage:
    def __init__(self, spacing, layer_edges, region_bounds=None, window=None):
        if not math.isfinite(spacing) or not .005 <= spacing <= .20:
            raise ValueError('Navigation cell spacing must be 0.005..0.20 m')
        self.spacing = spacing
        self.edges = layer_edges
        if region_bounds is not None:
            if len(region_bounds) != 3 or any(len(axis) != 2 or not all(math.isfinite(x) for x in axis)
                    or axis[0] >= axis[1] for axis in region_bounds):
                raise ValueError('Finite x/y/z region intervals required')
        self.region_bounds = region_bounds
        self.window = window
        self.cells = {}
        self.recorded_segments = defaultdict(list)
        self.max_pose_spacing_m = 0.
        self.pose_segments = 0

    def layer(self, point):
        if self.window is None and (point[1] < 0 or point[2] < 0):
            return None
        if self.region_bounds is not None and any(not lo <= value < hi
                for value,(lo,hi) in zip(point,self.region_bounds)):
            return None
        r = math.sqrt(sum(x*x for x in point))
        layer=next((i for i in range(3) if self.edges[i] <= r < self.edges[i+1]), None)
        return layer if layer is not None and (self.window is None or self.window.contains(point,layer)) else None

    def _cell(self, group, point):
        layer = self.layer(point)
        if layer is None:
            return None
        key = (*group, layer, *(math.floor(x/self.spacing) for x in point))
        return self.cells.setdefault(key, dict(pose_length_m=0., recorded_length_m=0.,
                    recording_seconds=0., direction_moment=np.zeros((3,3)), visits=0))

    def add(self, group, a, b, seconds=0., recorded=False):
        if not all(math.isfinite(x) for x in (*a, *b, seconds)) or seconds < 0:
            raise ValueError('Finite trajectory endpoints/duration required')
        delta = [y-x for x,y in zip(a,b)]
        length = math.sqrt(sum(x*x for x in delta))
        if not recorded and length >= 1e-12:
            self.pose_segments += 1
            self.max_pose_spacing_m = max(self.max_pose_spacing_m, length)
        if length < 1e-12:
            cell = self._cell(group, a)
            if cell is not None and recorded:
                cell['recording_seconds'] += seconds
                self.recorded_segments[tuple(group)].append((tuple(a),tuple(b)))
            return
        cuts = {0., 1.}
        if self.window is not None:
            cuts.update(self.window.cuts(a,b))
        if self.region_bounds is not None:
            for start,change,bounds in zip(a,delta,self.region_bounds):
                if abs(change) > 1e-12:
                    for bound in bounds:
                        t = (bound-start)/change
                        if 0 < t < 1:
                            cuts.add(t)
        for start, end, change in zip(a,b,delta):
            if abs(change) < 1e-12:
                continue
            for k in range(math.floor(min(start,end)/self.spacing)+1,
                           math.floor(max(start,end)/self.spacing)+1):
                t = (k*self.spacing-start)/change
                if 0 < t < 1:
                    cuts.add(t)
        # Split at each shell surface, including a segment that enters and
        # exits a layer even when both endpoints lie elsewhere.
        aa = length*length
        bb = 2*sum(x*y for x,y in zip(a,delta))
        for radius in self.edges:
            cc = sum(x*x for x in a)-radius*radius
            discriminant = bb*bb-4*aa*cc
            if discriminant >= 0:
                for t in ((-bb-math.sqrt(discriminant))/(2*aa),(-bb+math.sqrt(discriminant))/(2*aa)):
                    if 0 < t < 1:
                        cuts.add(t)
        # The quarter-space boundaries are also Cartesian grid planes.
        direction = np.asarray(delta)/length
        moment = np.outer(direction,direction)
        cuts = sorted(float(v) for v in cuts)
        for lo,hi in zip(cuts,cuts[1:]):
            if hi-lo < 1e-12:
                continue
            point = [x+(lo+hi)/2*d for x,d in zip(a,delta)]
            cell = self._cell(group, point)
            if cell is None:
                continue
            fraction = hi-lo
            if recorded:
                self.recorded_segments[tuple(group)].append((
                    tuple(x+lo*d for x,d in zip(a,delta)),
                    tuple(x+hi*d for x,d in zip(a,delta))))
                cell['recorded_length_m'] += length*fraction
                cell['recording_seconds'] += seconds*fraction
                cell['direction_moment'] += moment*length*fraction
                cell['visits'] += 1
            else:
                cell['pose_length_m'] += length*fraction

    def summaries(self):
        groups = defaultdict(list)
        for key,cell in self.cells.items():
            hz,route,layer,*index = key
            eigen = np.linalg.eigvalsh(cell['direction_moment'])
            trace = float(eigen.sum())
            groups[(hz,route,layer)].append(dict(index=index,
                centre_m=[(i+.5)*self.spacing for i in index],
                pose_length_m=cell['pose_length_m'], recorded_length_m=cell['recorded_length_m'],
                recording_seconds=cell['recording_seconds'],
                path_length_density_m_per_m3=cell['recorded_length_m']/self.spacing**3,
                second_direction_fraction=float(eigen[-2]/trace) if trace > 0 else 0.))
        return groups

    def distance_to_recorded_path(self, group, point):
        """Exact nearest point-to-segment distance, independent of grid/view.

        Call on physical target positions once the intended region is defined.
        This is geometric path proximity, not acoustic interpolation validity.
        """
        best = math.inf
        for a,b in self.recorded_segments.get(tuple(group), []):
            if self.layer(point) is None:
                continue
            delta = [y-x for x,y in zip(a,b)]
            denominator = sum(x*x for x in delta)
            t = max(0.,min(1.,sum((x-y)*d for x,y,d in zip(point,a,delta))/denominator)) if denominator else 0.
            distance = math.sqrt(sum((x-y-t*d)**2 for x,y,d in zip(point,a,delta)))
            best = min(best,distance)
        return None if math.isinf(best) else best

    def snapshot(self):
        return dict(basis='metric_3d_capsule_trajectory', spacing_m=self.spacing,
                    region_bounds_m=self.region_bounds,
                    head_region_configured=self.region_bounds is not None,
                    pose_segments=self.pose_segments, max_pose_segment_spacing_m=self.max_pose_spacing_m,
                    coverage_fraction=None,
                    note='No global completeness percentage until physical scan region and spatial gap target are defined. Repeated path length is not new spatial coverage.',
                    groups=[dict(hz=k[0],route=k[1],layer=k[2],
                        visited_spatial_cells=len(v),recorded_spatial_cells=int(sum(c['recording_seconds']>0 for c in v)),
                        recorded_path_length_m=sum(c['recorded_length_m'] for c in v),
                        multidirectional_cells=sum(c['second_direction_fraction'] >= .1 for c in v),
                        cells=v) for k,v in sorted(self.summaries().items())])
