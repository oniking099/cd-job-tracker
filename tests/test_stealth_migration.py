"""
测试 playwright-stealth 1.x → 2.x 迁移（方案 B）。

验证要点：
1. playwright-stealth 2.x 正确安装
2. Stealth 类倒入正常
3. 新的 API 参数（navigator_webdriver）可用
4. 旧的依赖（StealthConfig / stealth_async / pkg_resources）不再使用
5. apply_stealth_async 方法存在
"""
from __future__ import annotations

import sys
import pytest


class TestStealthImport:
    """验证 playwright-stealth 2.x 导入"""

    def test_import_stealth(self) -> None:
        """Stealth 类可以正常导入"""
        from playwright_stealth import Stealth
        assert Stealth is not None

    def test_old_api_not_imported(self) -> None:
        """StealthConfig 和 stealth_async 在 2.x 中不存在"""
        with pytest.raises(ImportError):
            from playwright_stealth import StealthConfig  # noqa: F401

        with pytest.raises(ImportError):
            from playwright_stealth import stealth_async  # noqa: F401

    def test_pkg_resources_not_imported(self) -> None:
        """playwright-stealth 2.x 不再依赖 pkg_resources（根因修复验证）"""
        import playwright_stealth
        # 检查模块及其子模块的导入中不包含 pkg_resources
        modules_to_check = list(sys.modules.keys())
        pkg_resources_loaded = any(
            "pkg_resources" in m for m in modules_to_check
        )
        # pkg_resources 可能被其他模块加载，但 playwright_stealth 自身不应该触发它
        # 直接检查 playwright_stealth 模块的引用
        stealth_modules = [m for m in modules_to_check if "playwright_stealth" in m]
        for mod_name in stealth_modules:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                # 检查该模块是否有 pkg_resources 属性
                assert not hasattr(mod, "pkg_resources"), (
                    f"{mod_name} 仍然引用了 pkg_resources"
                )

    def test_version_2x(self) -> None:
        """版本号应为 2.x"""
        import importlib.metadata
        version = importlib.metadata.version("playwright-stealth")
        major = int(version.split(".")[0])
        assert major >= 2, f"期望版本 2.x，实际 {version}"


class TestStealthAPI:
    """验证 Stealth 2.x API"""

    def test_instantiate_with_new_params(self) -> None:
        """使用 2.x 参数名实例化 Stealth"""
        from playwright_stealth import Stealth
        stealth = Stealth(
            navigator_webdriver=True,
            webgl_vendor=True,
            chrome_app=True,
            chrome_csi=True,
            chrome_load_times=True,
            chrome_runtime=True,
            iframe_content_window=True,
            media_codecs=True,
            navigator_hardware_concurrency=4,
            navigator_languages=True,
            navigator_permissions=True,
            navigator_platform=True,
            navigator_plugins=True,
            navigator_user_agent=False,
            navigator_vendor=True,
            hairline=True,
        )
        assert stealth is not None

    def test_apply_stealth_async_exists(self) -> None:
        """Stealth 实例有 apply_stealth_async 方法"""
        from playwright_stealth import Stealth
        stealth = Stealth()
        assert hasattr(stealth, "apply_stealth_async")
        assert callable(stealth.apply_stealth_async)

    def test_outerdimensions_removed(self) -> None:
        """outerdimensions 参数在 2.x 中已被移除"""
        from playwright_stealth import Stealth
        with pytest.raises(TypeError):
            Stealth(outerdimensions=True)  # type: ignore[call-arg]

    def test_webdriver_param_renamed(self) -> None:
        """旧参数名 webdriver 不可用，必须用 navigator_webdriver"""
        from playwright_stealth import Stealth
        with pytest.raises(TypeError):
            Stealth(webdriver=True)  # type: ignore[call-arg]

    def test_default_construction(self) -> None:
        """无参数构造 Stealth 不报错"""
        from playwright_stealth import Stealth
        stealth = Stealth()
        assert stealth is not None
        assert hasattr(stealth, "navigator_webdriver")

    def test_stealth_attributes(self) -> None:
        """验证 Stealth 实例包含预期属性"""
        from playwright_stealth import Stealth
        stealth = Stealth(
            navigator_webdriver=True,
            webgl_vendor=True,
            chrome_app=True,
            chrome_csi=True,
            chrome_load_times=True,
            chrome_runtime=True,
            iframe_content_window=True,
            media_codecs=True,
            navigator_hardware_concurrency=4,
            navigator_languages=True,
            navigator_permissions=True,
            navigator_platform=True,
            navigator_plugins=True,
            navigator_user_agent=False,
            navigator_vendor=True,
            hairline=True,
        )
        # 参数名应与 base.py 中使用的一致
        assert stealth.navigator_webdriver is True
        assert stealth.webgl_vendor is True
        assert stealth.chrome_app is True
        assert stealth.chrome_csi is True
        assert stealth.chrome_load_times is True
        assert stealth.chrome_runtime is True
        assert stealth.iframe_content_window is True
        assert stealth.media_codecs is True
        assert stealth.navigator_hardware_concurrency == 4
        assert stealth.navigator_languages is True
        assert stealth.navigator_permissions is True
        assert stealth.navigator_platform is True
        assert stealth.navigator_plugins is True
        assert stealth.navigator_user_agent is False
        assert stealth.navigator_vendor is True
        assert stealth.hairline is True


class TestBaseScraperCompatibility:
    """验证 src/scrapers/base.py 与新 API 的兼容性"""

    def test_base_imports_succeed(self) -> None:
        """base.py 导入不报错"""
        from src.scrapers.base import BaseScraper
        assert BaseScraper is not None

    def test_stealth_import_in_base(self) -> None:
        """base.py 使用了正确的 Stealth 导入"""
        import src.scrapers.base as base_mod
        assert hasattr(base_mod, "Stealth")
        # 确保没有旧的 StealthConfig 或 stealth_async 引用
        assert not hasattr(base_mod, "StealthConfig")
        assert not hasattr(base_mod, "inject_stealth")
