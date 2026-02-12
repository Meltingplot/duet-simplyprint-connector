"""Tests for the task module."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from meltingplot.duet_simplyprint_connector.task import async_task, async_supress


@pytest.mark.asyncio
async def test_async_task_creates_task_and_adds_to_set():
    """Test that async_task wraps a function into a background task."""
    obj = Mock()
    obj.event_loop = asyncio.get_running_loop()
    obj._background_task = set()

    called = asyncio.Event()

    @async_task
    async def my_func(self):
        called.set()

    task = await my_func(obj)
    assert task in obj._background_task
    await task
    assert called.is_set()
    # done callback should have discarded the task
    await asyncio.sleep(0)
    assert task not in obj._background_task


@pytest.mark.asyncio
async def test_async_task_logs_exception():
    """Test that async_task logs exceptions instead of propagating."""
    obj = Mock()
    obj.event_loop = asyncio.get_running_loop()
    obj._background_task = set()

    @async_task
    async def failing_func(self):
        raise ValueError("test error")

    task = await failing_func(obj)
    await task
    obj.logger.exception.assert_called_once()
    assert "async function" in obj.logger.exception.call_args[0][0]


@pytest.mark.asyncio
async def test_async_supress_suppresses_exception():
    """Test that async_supress catches and logs exceptions."""
    obj = Mock()
    obj.logger = Mock()

    @async_supress
    async def failing_func(self):
        raise RuntimeError("oops")

    # Should not raise
    await failing_func(obj)
    obj.logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_async_supress_closes_duet_on_cancelled():
    """Test that async_supress closes duet on CancelledError."""
    obj = Mock()
    obj.duet = AsyncMock()

    @async_supress
    async def cancellable_func(self):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancellable_func(obj)

    obj.duet.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_supress_normal_execution():
    """Test that async_supress allows normal execution."""
    obj = Mock()
    result = []

    @async_supress
    async def normal_func(self):
        result.append(42)

    await normal_func(obj)
    assert result == [42]
