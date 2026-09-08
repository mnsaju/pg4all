from app.core import hardware, workloads
from app.core.conf_generator import generate_conf, render_conf


def test_oltp_medium_shared_buffers_is_quarter_of_ram():
    workload = workloads.get("oltp")
    tier = hardware.get("medium")  # 16 GB
    settings = generate_conf(workload, tier)
    assert settings["shared_buffers"] == "4194304kB"  # 16GB / 4


def test_desktop_uses_smaller_shared_buffers_fraction():
    workload = workloads.get("desktop")
    tier = hardware.get("small")  # 4 GB
    settings = generate_conf(workload, tier)
    assert settings["shared_buffers"] == "262144kB"  # 4GB / 16


def test_dw_recommends_large_tier():
    tier = hardware.recommend_for_workload("dw")
    assert tier.key == "large"


def test_render_conf_produces_key_value_lines():
    workload = workloads.get("web")
    tier = hardware.get("medium")
    settings = generate_conf(workload, tier)
    text = render_conf(settings)
    assert "max_connections = 200" in text
