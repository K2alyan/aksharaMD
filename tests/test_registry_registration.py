from aksharamd.context import CompilationContext
from aksharamd.plugins import registry
from aksharamd.plugins.base import CleanerPlugin, ValidatorPlugin


def test_late_registration_executes_in_priority_order(monkeypatch):
    monkeypatch.setattr(registry, "_plugin_classes", [])
    monkeypatch.setattr(registry, "_plugin_cache", {})
    calls = []

    class ExistingCleaner(CleanerPlugin):
        name = "existing"
        priority = 20

        def execute(self, ctx):
            calls.append(self.name)
            return ctx

    class LateCleaner(CleanerPlugin):
        name = "late"
        priority = 10

        def execute(self, ctx):
            calls.append(self.name)
            return ctx

    registry.register_plugin(ExistingCleaner)
    registry.get_plugins_of_type(CleanerPlugin)
    registry.register_plugin(LateCleaner)
    ctx = CompilationContext(source="test.txt", output_dir="unused")
    for plugin in registry.get_plugins_of_type(CleanerPlugin):
        ctx = plugin.execute(ctx)
    assert calls == ["late", "existing"]


def test_registration_preserves_unrelated_and_duplicate_stage_instances(monkeypatch):
    monkeypatch.setattr(registry, "_plugin_classes", [])
    monkeypatch.setattr(registry, "_plugin_cache", {})

    class Cleaner(CleanerPlugin):
        name = "cleaner"

        def execute(self, ctx):
            return ctx

    class Validator(ValidatorPlugin):
        name = "validator"

        def execute(self, ctx):
            return ctx

    registry.register_plugin(Validator)
    validators = registry.get_plugins_of_type(ValidatorPlugin)
    registry.register_plugin(Cleaner)
    cleaners = registry.get_plugins_of_type(CleanerPlugin)
    registry.register_plugin(Cleaner)
    assert registry.get_plugins_of_type(CleanerPlugin) is cleaners
    assert len(cleaners) == 1
    assert registry.get_plugins_of_type(ValidatorPlugin) is validators
