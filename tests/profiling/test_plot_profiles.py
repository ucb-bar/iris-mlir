"""Metrics integrity gates for the standalone plotting tool."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('plot_profiles', ROOT / 'tools/profiling/plot_profiles.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def record(route='RVV', **kw):
    return dict(id=route, model='tinyllama', part='q', phase='decode', case='q_m1',
                route=route, variant=route, m=1, n=8, k=8, median_cycles=10,
                cycle_samples=[9, 10, 11], macs=64, logical_bytes=104,
                paired_group='q_fixture', scope='model_shaped_operator',
                basis='measured_guest_mcycle', physical_busy=None,
                utilization={'value': None, 'kind': 'unavailable'}, **kw)


def test_ratio_and_whiskers_are_observed_bounds_not_confidence_intervals():
    a, b = record(), record('VME')
    a.update(median_cycles=20, cycle_samples=[18, 20, 22])
    pair, = P.pair_records([a, b])
    assert pair['speedup'] == 2
    assert pair['low'] == 18 / 11
    assert pair['high'] == 22 / 9


def test_different_work_or_geometry_cannot_be_compared():
    a, b = record(), record('VME')
    for key in ['m', 'n', 'k', 'macs', 'logical_bytes']:
        original = b[key]
        b[key] += 1
        with pytest.raises(ValueError, match='incompatible'):
            P.pair_records([a, b])
        b[key] = original


def test_ambiguous_route_is_not_silently_selected():
    with pytest.raises(ValueError, match='ambiguous'):
        P.pair_records([record(), record(), record('VME')])


def test_functional_and_hardware_counts_cannot_be_paired():
    a, b = record(), record('VME')
    b['basis'] = 'functional_instruction_count'
    with pytest.raises(ValueError, match='basis'):
        P.pair_records([a, b])


def test_bad_medians_and_duplicate_ids_refused():
    r = record()
    r['median_cycles'] = 7
    with pytest.raises(ValueError, match='median mismatch'):
        P.validate_records({'records': [r]})
    with pytest.raises(ValueError, match='duplicate'):
        P.validate_records({'records': [record(), record()]})


def test_logical_bytes_never_substitute_for_measured_boundary_traffic():
    r = record()
    assert P.roof_point(r) == (128 / 104, 12.8)
    assert P.roof_point(r, traffic_field='measured_boundary_bytes') is None


def test_lane_fill_does_not_imply_physical_busy():
    r = record()
    r['utilization'] = {'value': 1.0, 'kind': 'untimed_lane_geometry', 'coverage': 1.0}
    P.validate_records({'records': [r]})
    assert r['physical_busy'] is None
    r['physical_busy'] = 1
    with pytest.raises(ValueError, match='independent physical evidence'):
        P.validate_records({'records': [r]})


def test_render_refuses_output_overwrite(tmp_path):
    with pytest.raises(FileExistsError):
        P.render({'records': []}, {}, tmp_path)


def test_numerical_contract_and_observer_modes_are_part_of_pair_identity():
    a, b = record(), record('VME')
    for key in ['numeric_contract', 'observer_mode', 'cohort', 'logical_bytes_basis']:
        b[key] = 'different'
        with pytest.raises(ValueError, match='incompatible'):
            P.pair_records([a, b])
        del b[key]
    b['paired_backend_math_compatible'] = False
    with pytest.raises(ValueError, match='unqualified arithmetic'):
        P.pair_records([a, b])


def test_memory_slopes_require_the_same_boundary_and_clock_basis():
    r = record()
    r.update(measured_boundary_bytes=200, traffic_boundary='AXI.D', cycle_basis='core_target_clock_edge')
    c = {'boundary': 'AXI.D', 'cycle_basis': 'memory_clock_edge', 'bytes_per_cycle': 8}
    assert P.matching_traffic([r], c) == []
    c['cycle_basis'] = 'core_target_clock_edge'
    assert P.matching_traffic([r], c) == [r]
    c['boundary'] = 'AXI.A'
    assert P.matching_traffic([r], c) == []
    del c['cycle_basis']
    assert P.matching_traffic([r], c) == []
