"""证书硬性要求过滤：仅保留环保工程师证，缺失或优先项不误删。"""
from src.filters.qualification import filter_qualification
from src.models import Job


def _job(requirements: str) -> Job:
    return Job(
        platform="测试",
        job_id="certificate-test",
        url="https://example.com/job/1",
        title="环境工程师",
        company="测试公司",
        requirements=requirements,
    )


def test_keeps_job_without_certificate_requirement() -> None:
    job = _job("本科及以上，3 年相关工作经验，熟悉废水处理工艺。")

    filter_qualification([job])

    assert not job.excluded


def test_keeps_required_environmental_engineer_certificate() -> None:
    job = _job("须持有注册环保工程师资格证书，能独立完成环评工作。")

    filter_qualification([job])

    assert not job.excluded


def test_excludes_required_non_environmental_certificate() -> None:
    job = _job("需具备注册安全工程师证书，有危化行业经验。")

    filter_qualification([job])

    assert job.excluded
    assert job.exclude_reason == "资格排除: certificate_non_environmental"


def test_excludes_job_requiring_environmental_and_other_certificate() -> None:
    job = _job("须持有环保工程师证书及一级建造师证书。")

    filter_qualification([job])

    assert job.excluded
    assert job.exclude_reason == "资格排除: certificate_non_environmental"


def test_keeps_optional_non_environmental_certificate() -> None:
    job = _job("持有一级建造师证书者优先，其他条件面议。")

    filter_qualification([job])

    assert not job.excluded


def test_excludes_required_registered_security_engineer_certificate() -> None:
    job = _job("需具备注册安全工程师证书，熟悉废气处理工艺。")

    filter_qualification([job])

    assert job.excluded
    assert job.exclude_reason == "资格排除: certificate_non_environmental"


def test_excludes_required_registered_accountant_certificate() -> None:
    job = _job("要求持有注册会计师证书，5 年以上相关工作经验。")

    filter_qualification([job])

    assert job.excluded


def test_excludes_registered_qualification_without_cert_character() -> None:
    job = _job("须取得注册安全工程师资格，能独立完成安全工作。")

    filter_qualification([job])

    assert job.excluded


def test_keeps_degree_certificate_requirement() -> None:
    """毕业证/学位证是学历材料，不是职业资格证，不能误伤。"""
    job = _job("需本科及以上学历，提供毕业证书及学位证书。")

    filter_qualification([job])

    assert not job.excluded
