from pathlib import Path

from voxlogica.sweep_artifact import build_artifact, parse_prints


def test_parse_prints_handles_pty_frames_and_nested_values():
    text = (
        '\x1b[2Kprogress\r'
        'sweep_parameter_names=["channel", "floor", "radius"]\r\n'
        'g04_score=0.8125\n'
        'g04_argbest=[2, 0.76, 18.0]\n'
    )
    parsed = parse_prints(text)
    assert parsed["g04_score"] == 0.8125
    assert parsed["g04_argbest"] == [2, 0.76, 18.0]


def test_build_artifact_maps_argbest_to_named_parameters():
    text = "\n".join(
        [
            'sweep_parameter_names=["channel", "floor"]',
            'sweep_channel_names=["raw", "n4"]',
            "m_param_grid=[[0.95, 0.72]]",
            "g00_score=0.9",
            "g00_argbest=[1, 0.72]",
            "g00_raw_M_surface_scores=[0.8]",
        ]
    )
    artifact = build_artifact(Path("trial.out"), text)
    assert artifact["schema"] == "voxlogica/sweep-results/v1"
    assert artifact["cases"]["g00"]["parameters"] == {"channel": 1, "floor": 0.72}
    assert artifact["prints"]["g00_raw_M_surface_scores"] == [0.8]
    assert len(artifact["source_sha256"]) == 64
