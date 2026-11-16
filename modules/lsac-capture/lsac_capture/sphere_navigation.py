"""Device-free, geometry-only guidance for a tracked spherical scan.

This is NOT an acoustic posterior.  Each radial layer has its own low-order
real-spherical-harmonic Gram matrix; samples in one layer cannot complete
another.  A 3 cm spatial bin contributes once and each angular cell has a
bounded contribution.  The regularized inverse is a planning metric, not a
confidence interval or a claim of acoustic/spatial resolution.

Coordinates are source-local metres with +Z up.  Actual sample radii are
retained.  The nominal shells only assign observations and propose targets.
No audio, tracker, playback, filesystem, or network operations are performed.
"""

import math
from threading import RLock

import numpy as np


def _real_harmonics(directions, order):
    """Orthonormal real SH, rescaled so each row has unit squared norm."""
    xyz = np.asarray(directions, dtype=float)
    xyz = np.atleast_2d(xyz)
    xyz = xyz / np.linalg.norm(xyz, axis=1)[:, None]
    z = np.clip(xyz[:, 2], -1., 1.)
    phi = np.arctan2(xyz[:, 1], xyz[:, 0])
    associated = {}
    associated[0, 0] = np.ones(len(xyz))
    root = np.sqrt(np.maximum(0., 1. - z * z))
    for m in range(1, order + 1):
        associated[m, m] = -(2 * m - 1) * root * associated[m - 1, m - 1]
    for m in range(order + 1):
        if m < order:
            associated[m + 1, m] = (2 * m + 1) * z * associated[m, m]
        for ell in range(m + 2, order + 1):
            associated[ell, m] = ((2 * ell - 1) * z * associated[ell - 1, m]
                                     - (ell + m - 1) * associated[ell - 2, m]) / (ell - m)
    columns = []
    for ell in range(order + 1):
        for signed_m in range(-ell, ell + 1):
            m = abs(signed_m)
            scale = math.sqrt((2 * ell + 1) / (4 * math.pi)
                              * math.factorial(ell - m) / math.factorial(ell + m))
            value = scale * associated[ell, m]
            if signed_m < 0:
                value = math.sqrt(2.) * value * np.sin(m * phi)
            elif signed_m > 0:
                value = math.sqrt(2.) * value * np.cos(m * phi)
            columns.append(value)
    return np.column_stack(columns) * math.sqrt(4 * math.pi / (order + 1) ** 2)


def _position(value):
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if result.shape != (3,) or not np.isfinite(result).all():
        return None
    return result


class SphereNavigator:
    """Incremental independent-layer SH geometry planner, never a confidence meter."""

    def __init__(self, radii_m, order=3, *, angular_cells=192,
                 spatial_bin_size_m=.03, shell_half_width_m=.08,
                 regularization=.1, min_z_m=None, max_z_m=None,
                 arrival_radius_m=.06, arc_span_degrees=18.,
                 max_bins_per_angular_cell=8):
        radii = np.asarray(radii_m, dtype=float)
        if (radii.ndim != 1 or not len(radii) or len(radii) > 8
                or not np.isfinite(radii).all() or np.any(radii <= 0)
                or np.any(np.diff(radii) <= 0)):
            raise ValueError('radii_m must contain 1..8 strictly increasing positive radii')
        if isinstance(order, bool) or int(order) != order or not 0 <= order <= 6:
            raise ValueError('order must be an integer from 0 to 6')
        if isinstance(angular_cells, bool) or int(angular_cells) != angular_cells or not 24 <= angular_cells <= 2048:
            raise ValueError('angular_cells must be an integer from 24 to 2048')
        for name, value in [('spatial_bin_size_m', spatial_bin_size_m),
                            ('shell_half_width_m', shell_half_width_m),
                            ('regularization', regularization),
                            ('arrival_radius_m', arrival_radius_m)]:
            if not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be positive and finite')
        if not math.isfinite(arc_span_degrees) or not 0 < arc_span_degrees <= 45:
            raise ValueError('arc_span_degrees must be in (0, 45]')
        if (isinstance(max_bins_per_angular_cell, bool)
                or int(max_bins_per_angular_cell) != max_bins_per_angular_cell
                or not 1 <= max_bins_per_angular_cell <= 64):
            raise ValueError('max_bins_per_angular_cell must be an integer from 1 to 64')
        self.radii = radii
        self.order = int(order)
        self.mode_count = (self.order + 1) ** 2
        self.spacing = float(spatial_bin_size_m)
        self.shell_half_width = float(shell_half_width_m)
        self.regularization = float(regularization)
        self.arrival_radius = float(arrival_radius_m)
        self.arc_span = math.radians(arc_span_degrees)
        self.max_bins_per_cell = int(max_bins_per_angular_cell)
        # Equal widths in z=sin(elevation) and azimuth give exactly equal
        # solid-angle cells.  Cell occupancy is not coverage of its interior.
        requested = int(angular_cells)
        divisors = [d for d in range(2, int(math.sqrt(requested)) + 1) if requested % d == 0]
        self.nz = max(divisors) if divisors else max(2, round(math.sqrt(requested / 2)))
        self.nphi = math.ceil(requested / self.nz)
        self.cell_count = self.nz * self.nphi
        directions = []
        for iz in range(self.nz):
            z = -1. + 2. * (iz + .5) / self.nz
            for iphi in range(self.nphi):
                phi = -math.pi + 2. * math.pi * (iphi + .5) / self.nphi
                horizontal = math.sqrt(1. - z * z)
                directions.append([horizontal * math.cos(phi), horizontal * math.sin(phi), z])
        self.directions = np.asarray(directions)
        self.target_basis = _real_harmonics(self.directions, self.order)
        self.target_moment = self.target_basis.T @ self.target_basis / self.cell_count
        self._mutex = RLock()
        self.min_z = self.max_z = None
        self._target = None
        self.set_reach_limits(min_z_m=min_z_m, max_z_m=max_z_m)
        self.reset()

    def reset(self):
        """Clear observations, skipped targets and lock; retain plan/reach limits."""
        with self._mutex:
            self._grams = [np.zeros((self.mode_count, self.mode_count)) for _ in self.radii]
            self._inverse = [np.eye(self.mode_count) / self.regularization for _ in self.radii]
            self._cell_bins = np.zeros((len(self.radii), self.cell_count), dtype=np.int64)
            self._bins = {}
            self._radii_observed = [[] for _ in self.radii]
            self._z_observed = [[] for _ in self.radii]
            self._target = None
            self._completed = set()
            self._skipped = set()
            self._revision = 0
            self.total_input_samples = 0
            self.rejected_samples = 0
            self.repeated_bin_samples = 0

    def set_reach_limits(self, *, min_z_m=None, max_z_m=None):
        """Change candidate height limits without deleting measured or missing caps."""
        for value in (min_z_m, max_z_m):
            if value is not None and not math.isfinite(value):
                raise ValueError('reach limits must be finite or None')
        if min_z_m is not None and max_z_m is not None and min_z_m >= max_z_m:
            raise ValueError('min_z_m must be lower than max_z_m')
        with self._mutex:
            self.min_z = None if min_z_m is None else float(min_z_m)
            self.max_z = None if max_z_m is None else float(max_z_m)
            if self._target is not None and any(
                    not self._reachable(point) for point in self._target['path_local_m']):
                self._target = None

    def _reachable(self, point):
        z = float(point[2])
        return ((self.min_z is None or z >= self.min_z - 1e-12)
                and (self.max_z is None or z <= self.max_z + 1e-12))

    def _cell_index(self, direction):
        iz = min(self.nz - 1, max(0, int((float(direction[2]) + 1.) * .5 * self.nz)))
        phi = math.atan2(float(direction[1]), float(direction[0]))
        iphi = min(self.nphi - 1, int((phi + math.pi) / (2. * math.pi) * self.nphi))
        return iz * self.nphi + iphi

    def add_sample(self, position_local_m, *, valid=True, recording=True):
        """Count a new recorded spatial bin once, retaining its actual radius.

        Return True only for a newly accepted bin.  Repeated callback/audio
        rates and unrecorded preview motion cannot manufacture information.
        Reach limits constrain suggested movement, not valid historical data.
        """
        with self._mutex:
            self.total_input_samples += 1
            point = _position(position_local_m)
            if not valid or not recording or point is None:
                self.rejected_samples += 1
                return False
            radius = float(np.linalg.norm(point))
            if radius <= 1e-12:
                self.rejected_samples += 1
                return False
            layer = int(np.argmin(np.abs(self.radii - radius)))
            if abs(float(self.radii[layer]) - radius) > self.shell_half_width + 1e-12:
                self.rejected_samples += 1
                return False
            # Arrival requires a valid recorded observation, never just UI pose.
            if (self._target is not None and layer == self._target['layer_index']
                    and np.linalg.norm(point - self._target['point_local_m']) <= self.arrival_radius):
                self._completed.add((layer, self._target['cell_index']))
                self._target = None
            key = tuple(int(v) for v in np.floor(point / self.spacing))
            if key in self._bins:
                self.repeated_bin_samples += 1
                return False
            direction = point / radius
            cell = self._cell_index(direction)
            count = int(self._cell_bins[layer, cell])
            self._bins[key] = dict(layer_index=layer, cell_index=cell,
                                   position_local_m=point.tolist(), radius_m=radius)
            self._cell_bins[layer, cell] += 1
            self._radii_observed[layer].append(radius)
            self._z_observed[layer].append(float(direction[2]))
            if count < self.max_bins_per_cell:
                row = _real_harmonics(direction, self.order)[0]
                self._grams[layer] += np.outer(row, row)
                inverse_row = self._inverse[layer] @ row
                self._inverse[layer] -= np.outer(inverse_row, inverse_row) / (1. + row @ inverse_row)
                self._inverse[layer] = (self._inverse[layer] + self._inverse[layer].T) * .5
            self._revision += 1
            return True

    def skip(self):
        """Skip the locked target until reset; does not mark it measured."""
        with self._mutex:
            if self._target is None:
                return False
            self._skipped.add((self._target['layer_index'], self._target['cell_index']))
            self._target = None
            return True

    def _metrics(self, layer):
        inverse = self._inverse[layer]
        projected = self.target_basis @ inverse
        variance = np.maximum(0., np.sum(projected * self.target_basis, axis=1))
        gain = np.maximum(0., np.sum((projected @ self.target_moment) * projected, axis=1) / (1. + variance))
        gain[self._cell_bins[layer] >= self.max_bins_per_cell] = 0.
        return variance, gain

    def _arc(self, layer, cell):
        """A short reachable great-circle arc beginning at the target.

        It is a local guide, not an obstacle-checked route from the current
        probe position.  No interpolation across the source is requested.
        """
        direction = self.directions[cell]
        radius = float(self.radii[layer])
        _, gains = self._metrics(layer)
        dots = np.clip(self.directions @ direction, -1., 1.)
        angles = np.arccos(dots)
        neighbors = [i for i in range(self.cell_count)
                     if 1e-6 < angles[i] <= self.arc_span * 1.6
                     and self._reachable(self.directions[i] * radius)
                     and (layer, i) not in self._skipped]
        neighbors.sort(key=lambda i: (-float(gains[i]), float(angles[i]), i))
        for neighbor in neighbors:
            tangent = self.directions[neighbor] - dots[neighbor] * direction
            tangent /= np.linalg.norm(tangent)
            end = min(self.arc_span, float(angles[neighbor]))
            path = [radius * (math.cos(t) * direction + math.sin(t) * tangent)
                    for t in np.linspace(0., end, 5)]
            if all(self._reachable(point) for point in path):
                return [point.tolist() for point in path]
        return [(direction * radius).tolist()]

    def _recommend(self, current):
        if self._target is not None:
            return
        best = None
        for layer, radius in enumerate(self.radii):
            _, gains = self._metrics(layer)
            for cell, direction in enumerate(self.directions):
                point = direction * radius
                if ((layer, cell) in self._skipped or (layer, cell) in self._completed
                        or not self._reachable(point) or gains[cell] <= 1e-15):
                    continue
                key = tuple(int(v) for v in np.floor(point / self.spacing))
                if key in self._bins:
                    continue
                distance = float(np.linalg.norm(point - current))
                cost = 1. + distance / .35 + abs(float(point[2] - current[2])) / .5
                score = float(gains[cell]) / cost
                if best is None or score > best[0]:
                    best = (score, layer, cell, point, float(gains[cell]), cost)
        if best is not None:
            score, layer, cell, point, gain, cost = best
            self._target = dict(layer_index=layer, cell_index=cell,
                                point_local_m=point.tolist(), path_local_m=self._arc(layer, cell),
                                reason='reduce_layer_spherical_harmonic_geometry_gap',
                                score=score, information_gain=gain, motion_cost=cost,
                                gain_basis='single_new_geometry_bin_target_variance_reduction',
                                path_role='local_arc_not_obstacle_checked_transit', locked=True,
                                selected_revision=self._revision)

    def snapshot(self, current_local_m=None, stale=False):
        """Return JSON-safe geometry state; stale tracking suppresses guidance."""
        with self._mutex:
            supplied = current_local_m is not None
            current = _position(current_local_m) if supplied else np.array([0., self.radii[0], 0.])
            stale = bool(stale or (supplied and current is None))
            layers = []
            for layer, radius in enumerate(self.radii):
                variance, gains = self._metrics(layer)
                reachable = np.array([self._reachable(d * radius) for d in self.directions])
                visited = self._cell_bins[layer] > 0
                cells = []
                for cell, direction in enumerate(self.directions):
                    cells.append(dict(cell_index=cell, direction=direction.tolist(),
                                      point_local_m=(direction * radius).tolist(),
                                      solid_angle_sr=4. * math.pi / self.cell_count,
                                      reachable=bool(reachable[cell]), visited=bool(visited[cell]),
                                      skipped=(layer, cell) in self._skipped,
                                      recorded_bin_count=int(self._cell_bins[layer, cell]),
                                      geometry_variance=float(variance[cell]),
                                      information_gain=float(gains[cell])))
                eigen = np.linalg.eigvalsh(self._grams[layer])
                threshold = max(1e-10, float(eigen[-1]) * 1e-8)
                positive = eigen[eigen > threshold]
                actual_radii = self._radii_observed[layer]
                z_values = self._z_observed[layer]
                layers.append(dict(layer_index=layer, radius_m=float(radius),
                                   recorded_bin_count=int(self._cell_bins[layer].sum()),
                                   geometric_rank=int(len(positive)), mode_count=self.mode_count,
                                   geometric_condition_number=(float(positive[-1] / positive[0])
                                                               if len(positive) == self.mode_count else None),
                                   geometry_risk=float(np.trace(self._inverse[layer] @ self.target_moment)),
                                   coverage_fraction=float(visited.mean()),
                                   reachable_fraction=float(reachable.mean()),
                                   reachable_coverage_fraction=(float(visited[reachable].mean())
                                                                if reachable.any() else None),
                                   unreachable_unvisited_fraction=float((~reachable & ~visited).mean()),
                                   actual_radius_min_m=min(actual_radii) if actual_radii else None,
                                   actual_radius_max_m=max(actual_radii) if actual_radii else None,
                                   observed_direction_z_min=min(z_values) if z_values else None,
                                   observed_direction_z_max=max(z_values) if z_values else None,
                                   north_cap_recorded_bins=sum(v >= .9 for v in z_values),
                                   south_cap_recorded_bins=sum(v <= -.9 for v in z_values), cells=cells))
            if not stale:
                self._recommend(current)
            recommendation = None
            if not stale and self._target is not None:
                recommendation = dict(self._target)
                recommendation['point_local_m'] = list(self._target['point_local_m'])
                recommendation['path_local_m'] = [list(p) for p in self._target['path_local_m']]
            return dict(schema='lsac-sphere-navigation-v1', basis='geometry_only',
                        acoustic_confidence_validated=False, verified=False,
                        coordinate_frame='source_local_z_up',
                        radial_model='independent_layer_geometry', order=self.order,
                        mode_count=self.mode_count, regularization=self.regularization,
                        prior_role='numerical_geometry_planning_only',
                        angular_grid=dict(kind='equal_solid_angle_z_azimuth',
                                          z_bins=self.nz, azimuth_bins=self.nphi,
                                          cells_per_layer=self.cell_count),
                        coverage_basis='angular_cell_occupancy_not_surface_completeness',
                        spatial_bin_size_m=self.spacing, shell_half_width_m=self.shell_half_width,
                        max_bins_per_angular_cell=self.max_bins_per_cell,
                        recording_bin_count=len(self._bins), total_input_samples=self.total_input_samples,
                        rejected_samples=self.rejected_samples, repeated_bin_samples=self.repeated_bin_samples,
                        skipped_target_count=len(self._skipped), revision=self._revision, stale=stale,
                        reach_limits=dict(min_z_m=self.min_z, max_z_m=self.max_z),
                        geometry_risk=float(sum(layer['geometry_risk'] for layer in layers)),
                        recommendation=recommendation,
                        recommendation_status=('tracking_stale' if stale else 'available' if recommendation else
                                               'no_unskipped_reachable_target'),
                        layers=layers,
                        limitations=['No audio SNR, phase, clock, source/room posterior or FOA calibration is used.',
                                     'Geometry variance and gain are unitless planning proxies, not acoustic uncertainty.',
                                     'Unreachable and skipped directions remain missing; a visited cell is not fully scanned.',
                                     'Paths are local arcs; no obstacle or operator/body geometry is available.'])
