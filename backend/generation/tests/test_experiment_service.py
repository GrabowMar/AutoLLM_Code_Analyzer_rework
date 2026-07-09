"""Tests for backend/generation/services/experiments.py."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.models import ScaffoldingTemplate
from backend.generation.models import TemplateBundle
from backend.generation.services import experiments as experiments_svc
from backend.generation.services.bundle_resolver import derive_experiment_seed
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _real_flask_bundle() -> TemplateBundle:
    """The real auto-seeded default bundle — has valid, resolvable block_refs."""
    return TemplateBundle.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def experiment(user):
    exp = ExperimentFactory(created_by=user, repeats=2)
    exp.app_requirements.add(AppRequirementTemplateFactory(), AppRequirementTemplateFactory())
    return exp


@pytest.fixture
def condition(experiment):
    bundle = _real_flask_bundle()
    assert bundle is not None, "system-scaffolding-standard must be auto-seeded"
    model = LLMModelFactory(input_price_per_token=0.000001, output_price_per_token=0.000002)
    return ExperimentConditionFactory(experiment=experiment, template_bundle=bundle, model=model)


class TestExpandMatrix:
    def test_matrix_size_is_conditions_times_apps_times_repeats(self, experiment, condition):
        cells = experiments_svc.expand_matrix(experiment)
        # 1 condition x 2 apps x 2 repeats
        assert len(cells) == 4

    def test_repeat_indices_cover_the_full_range(self, experiment, condition):
        cells = experiments_svc.expand_matrix(experiment)
        repeats_per_app = {}
        for cell in cells:
            repeats_per_app.setdefault(cell.app_requirement.id, set()).add(cell.repeat_index)
        for repeats in repeats_per_app.values():
            assert repeats == {0, 1}

    def test_empty_when_no_conditions(self, experiment):
        assert experiments_svc.expand_matrix(experiment) == []


class TestPreviewExperiment:
    def test_preview_reports_total_jobs_and_nonzero_cost(self, experiment, condition):
        preview = experiments_svc.preview_experiment(experiment)
        assert preview["total_jobs"] == 4
        assert preview["conditions"] == 1
        assert preview["apps"] == 2
        assert preview["estimated_cost"] > 0
        assert len(preview["per_condition"]) == 1
        assert preview["per_condition"][0]["jobs"] == 4  # 2 apps x 2 repeats

    def test_preview_zero_cost_when_model_has_no_pricing(self, experiment):
        bundle = _real_flask_bundle()
        free_model = LLMModelFactory(input_price_per_token=0.0, output_price_per_token=0.0)
        ExperimentConditionFactory(experiment=experiment, template_bundle=bundle, model=free_model)

        preview = experiments_svc.preview_experiment(experiment)

        assert preview["estimated_cost"] == 0.0


class TestLaunchExperiment:
    def test_launch_creates_one_job_per_cell(self, experiment, condition, user):
        with mock.patch("backend.generation.services.experiments.dispatch_job") as dispatch:
            batch = experiments_svc.launch_experiment(experiment, user)

        assert batch.total_jobs == 4
        assert GenerationJob.objects.filter(experiment=experiment).count() == 4
        assert dispatch.call_count == 4

    def test_launch_sets_scaffolding_template_from_bundle_stack(self, experiment, condition, user):
        """resolve_stack_slug() reads job.scaffolding_template — must not be null."""
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        jobs = GenerationJob.objects.filter(experiment=experiment)
        expected = ScaffoldingTemplate.objects.get(slug="flask-react")
        assert all(job.scaffolding_template_id == expected.id for job in jobs)

    def test_launch_marks_experiment_running(self, experiment, condition, user):
        assert experiment.status == Experiment.Status.DRAFT
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.RUNNING

    def test_launch_is_idempotent_when_rerun_with_nothing_failed(self, experiment, condition, user):
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)
            # jobs are "pending" (never executed) — still non-failed, so a
            # second launch must not create duplicates.
            experiments_svc.launch_experiment(experiment, user)

        assert GenerationJob.objects.filter(experiment=experiment).count() == 4

    def test_launch_resumes_only_failed_cells(self, experiment, condition, user):
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        jobs = list(GenerationJob.objects.filter(experiment=experiment))
        jobs[0].status = GenerationJob.Status.FAILED
        jobs[0].save(update_fields=["status"])

        with mock.patch("backend.generation.services.experiments.dispatch_job") as dispatch:
            experiments_svc.launch_experiment(experiment, user)

        # Only the failed cell gets a fresh job; the other 3 are left alone.
        assert GenerationJob.objects.filter(experiment=experiment).count() == 5
        assert dispatch.call_count == 1

    def test_launch_derives_deterministic_seeds_from_base_seed(self, experiment, condition, user):
        experiment.base_seed = 42
        experiment.save(update_fields=["base_seed"])

        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        jobs = list(GenerationJob.objects.filter(experiment=experiment))
        seeds = {job.experiment_seed for job in jobs}
        assert None not in seeds
        assert len(seeds) == 4  # all distinct across (app, repeat) pairs
        # And genuinely deterministic: recomputing from the same cell identity
        # reproduces exactly what got stored on the job.
        for job in jobs:
            expected = derive_experiment_seed(42, job.condition_id, job.app_requirement_id, job.repeat_index)
            assert job.experiment_seed == expected

    def test_launch_without_base_seed_still_sets_a_seed(self, experiment, condition, user):
        assert experiment.base_seed is None
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        seeds = GenerationJob.objects.filter(experiment=experiment).values_list("experiment_seed", flat=True)
        assert all(seed is not None for seed in seeds)

    def test_launch_applies_condition_param_overrides(self, experiment, user):
        bundle = _real_flask_bundle()
        model = LLMModelFactory()
        cond = ExperimentConditionFactory(
            experiment=experiment,
            template_bundle=bundle,
            model=model,
            param_overrides={"temperature": 0.9, "max_tokens": 4000},
        )
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        jobs = GenerationJob.objects.filter(experiment=experiment, condition=cond)
        assert all(job.temperature == 0.9 for job in jobs)
        assert all(job.max_tokens == 4000 for job in jobs)


class TestExperimentStatus:
    def test_status_reports_progress_grid(self, experiment, condition, user):
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        status = experiments_svc.experiment_status(experiment)

        assert status["total_cells"] == 4
        assert status["jobs_created"] == 4
        assert status["by_status"] == {"pending": 4}
        assert len(status["grid"]) == 2  # 1 condition x 2 apps


class TestExportExperiment:
    def test_export_includes_config_conditions_and_jobs(self, experiment, condition, user):
        with mock.patch("backend.generation.services.experiments.dispatch_job"):
            experiments_svc.launch_experiment(experiment, user)

        payload = experiments_svc.export_experiment(experiment)

        assert payload["experiment"]["name"] == experiment.name
        assert len(payload["conditions"]) == 1
        assert len(payload["app_requirements"]) == 2
        assert len(payload["jobs"]) == 4
        assert all(job["experiment_seed"] is not None for job in payload["jobs"])
