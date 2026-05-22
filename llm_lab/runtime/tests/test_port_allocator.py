"""Tests for port allocation logic."""

import pytest

from llm_lab.runtime.models import PortAllocation
from llm_lab.runtime.services.port_allocator import allocate
from llm_lab.runtime.services.port_allocator import release
from llm_lab.runtime.tests.factories import ContainerInstanceFactory


@pytest.mark.django_db
def test_allocate_first_port():
    """First allocation should return 9001 when nothing is in DB."""
    PortAllocation.objects.all().delete()
    port = allocate()
    assert port == 9001


@pytest.mark.django_db
def test_allocate_second_port_increments():
    """Second allocation should increment the port."""
    PortAllocation.objects.all().delete()
    allocate()
    port = allocate()
    assert port == 9002


@pytest.mark.django_db
def test_allocate_respects_existing_db_rows():
    """Allocation skips ports already recorded in the DB."""
    PortAllocation.objects.all().delete()
    PortAllocation.objects.create(app_port=9001)
    port = allocate()
    assert port == 9002


@pytest.mark.django_db
def test_release_removes_allocation():
    """release() should delete the PortAllocation row."""
    PortAllocation.objects.all().delete()
    container = ContainerInstanceFactory(app_port=9001)
    alloc = PortAllocation.objects.create(app_port=9001, container=container)
    assert alloc.pk is not None
    release(container)
    assert not PortAllocation.objects.filter(pk=alloc.pk).exists()
