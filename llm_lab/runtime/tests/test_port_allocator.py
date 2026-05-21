"""Tests for port allocation logic."""

import pytest

from llm_lab.runtime.models import PortAllocation
from llm_lab.runtime.services.port_allocator import allocate_pair
from llm_lab.runtime.services.port_allocator import release
from llm_lab.runtime.tests.factories import ContainerInstanceFactory


@pytest.mark.django_db
def test_allocate_first_pair():
    """First allocation should return (5001, 8001) when nothing is in DB."""
    PortAllocation.objects.all().delete()
    backend, frontend = allocate_pair()
    assert backend == 5001
    assert frontend == 8001


@pytest.mark.django_db
def test_allocate_second_pair_increments():
    """Second allocation should increment both ports."""
    PortAllocation.objects.all().delete()
    allocate_pair()
    backend, frontend = allocate_pair()
    assert backend == 5002
    assert frontend == 8002


@pytest.mark.django_db
def test_allocate_respects_existing_db_rows():
    """Allocation skips ports already recorded in the DB."""
    PortAllocation.objects.all().delete()
    PortAllocation.objects.create(backend_port=5001, frontend_port=8001)
    backend, frontend = allocate_pair()
    assert backend == 5002
    assert frontend == 8002


@pytest.mark.django_db
def test_release_removes_allocation():
    """release() should delete the PortAllocation row."""
    PortAllocation.objects.all().delete()
    container = ContainerInstanceFactory(backend_port=5001, frontend_port=8001)
    alloc = PortAllocation.objects.create(
        backend_port=5001,
        frontend_port=8001,
        container=container,
    )
    assert alloc.pk is not None
    release(container)
    assert not PortAllocation.objects.filter(pk=alloc.pk).exists()
