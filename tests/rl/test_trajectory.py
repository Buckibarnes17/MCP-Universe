"""Tests for mcpuniverse.rl.trajectory module."""

import pytest

from mcpuniverse.rl.trajectory import TrajectoryResult, TrajectoryStep


class TestTrajectoryStep:
    """Tests for TrajectoryStep dataclass."""

    def test_basic_creation(self):
        step = TrajectoryStep(step_type="thought", content="Let me think...")
        assert step.step_type == "thought"
        assert step.content == "Let me think..."
        assert step.metadata == {}

    def test_with_metadata(self):
        step = TrajectoryStep(
            step_type="action",
            content='{"tool": "calculator", "args": {"a": 1}}',
            metadata={"tool_name": "calculator", "duration_ms": 150},
        )
        assert step.step_type == "action"
        assert step.metadata["tool_name"] == "calculator"
        assert step.metadata["duration_ms"] == 150

    def test_step_types(self):
        for stype in ["thought", "action", "action_input", "result", "answer", "error"]:
            step = TrajectoryStep(step_type=stype, content="test")
            assert step.step_type == stype

    def test_to_dict(self):
        step = TrajectoryStep(
            step_type="answer",
            content="The answer is 42",
            metadata={"confidence": 0.95},
        )
        if hasattr(step, "to_dict"):
            d = step.to_dict()
            assert d["type"] == "answer"
            assert d["content"] == "The answer is 42"


class TestTrajectoryResult:
    """Tests for TrajectoryResult dataclass."""

    def test_basic_creation(self):
        result = TrajectoryResult(
            instance_id="task_001",
            trajectory_id=0,
            response="The answer is 4",
            reward=1.0,
            finish_reason="completed",
        )
        assert result.instance_id == "task_001"
        assert result.trajectory_id == 0
        assert result.response == "The answer is 4"
        assert result.reward == 1.0
        assert result.finish_reason == "completed"

    def test_default_fields(self):
        result = TrajectoryResult(
            instance_id="t1",
            trajectory_id=0,
            response="ok",
            reward=0.5,
            finish_reason="done",
        )
        assert result.error is None
        assert result.trace_id is None
        assert result.trace_records == []
        assert result.full_trace_text == ""
        assert result.prompt_text == ""
        assert result.output_text == ""
        assert result.output_segments == []
        assert result.num_steps == 0
        assert result.num_tool_calls == 0
        assert result.running_time == 0.0
        assert result.rollout_mode == "text"
        assert result.token_ids == []
        assert result.token_segments == []
        assert result.trainable_mask == []

    def test_with_all_fields(self):
        result = TrajectoryResult(
            instance_id="task_002",
            trajectory_id=1,
            response="Weather is sunny",
            reward=0.8,
            finish_reason="max_iterations",
            error=None,
            trace_id="trace_123",
            full_trace_text="<|start|>system...",
            prompt_text="System prompt",
            output_text="Assistant response",
            output_segments=[
                {"role": "assistant", "content": "sunny", "trainable": True},
            ],
            num_steps=3,
            num_tool_calls=1,
            running_time=2.5,
            rollout_mode="token",
            token_ids=[1, 2, 3, 4, 5],
            trainable_mask=[False, False, True, True, True],
        )
        assert result.num_steps == 3
        assert result.num_tool_calls == 1
        assert result.running_time == 2.5
        assert result.rollout_mode == "token"
        assert len(result.token_ids) == 5
        assert len(result.trainable_mask) == 5

    def test_with_error(self):
        result = TrajectoryResult(
            instance_id="t3",
            trajectory_id=0,
            response="",
            reward=0.0,
            finish_reason="error",
            error="Timeout during tool call",
        )
        assert result.error == "Timeout during tool call"
        assert result.reward == 0.0

    def test_to_dict(self):
        result = TrajectoryResult(
            instance_id="t4",
            trajectory_id=0,
            response="Hello",
            reward=1.0,
            finish_reason="completed",
            num_steps=2,
            num_tool_calls=0,
        )
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["instance_id"] == "t4"
        assert d["response"] == "Hello"
        assert d["reward"] == 1.0
        assert d["num_steps"] == 2

    def test_get_training_text(self):
        result = TrajectoryResult(
            instance_id="t5",
            trajectory_id=0,
            response="answer",
            reward=1.0,
            finish_reason="done",
            prompt_text="prompt part",
            output_text="output part",
        )
        text = result.get_training_text()
        assert isinstance(text, str)

    def test_get_training_tokens(self):
        result = TrajectoryResult(
            instance_id="t6",
            trajectory_id=0,
            response="answer",
            reward=1.0,
            finish_reason="done",
            token_ids=[10, 20, 30],
            trainable_mask=[False, True, True],
            token_segments=[{"type": "prompt"}, {"type": "output"}, {"type": "output"}],
        )
        tokens = result.get_training_tokens()
        assert isinstance(tokens, dict)
        assert "token_ids" in tokens
        assert "trainable_mask" in tokens
