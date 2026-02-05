"""
LLM wrapper that tracks token usage.

This module provides a wrapper around BaseLLM that counts tokens for all generate calls.
"""
import os
from typing import List, Dict, Any, Optional
from mcpuniverse.llm.base import BaseLLM


class TokenTrackingLLM:
    """
    Wrapper around BaseLLM that tracks token usage.

    Attributes:
        _llm: The underlying LLM instance.
        _total_input_tokens: Total input tokens used.
        _total_output_tokens: Total output tokens used.
        _call_count: Number of LLM calls made.
        _default_timeout: Default HTTP timeout in seconds for LLM requests.
    """

    def __init__(self, llm: BaseLLM, default_timeout: Optional[int] = None):
        """
        Initialize the token tracking wrapper.

        Args:
            llm: The BaseLLM instance to wrap.
            default_timeout: Default HTTP timeout in seconds. If None, uses
                MCPEVOLVE_LLM_TIMEOUT environment variable or defaults to 120.
        """
        self._llm = llm
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0

        # Determine timeout: explicit param > env var > default 120
        if default_timeout is not None:
            self._default_timeout = default_timeout
        else:
            env_timeout = os.getenv('MCPEVOLVE_LLM_TIMEOUT')
            if env_timeout:
                self._default_timeout = int(env_timeout)
            else:
                self._default_timeout = 120

    async def generate_async(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        Generate response and track token usage.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            **kwargs: Additional arguments for the LLM.

        Returns:
            Generated response string.
        """
        # Inject default timeout if not present in kwargs
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self._default_timeout

        # Count input tokens
        from mcpevolve.agent.react_postprocess import count_tokens
        input_text = " ".join([msg.get("content", "") for msg in messages])
        input_tokens = count_tokens(input_text)

        # Call underlying LLM
        response = await self._llm.generate_async(messages, **kwargs)

        # Count output tokens
        output_tokens = count_tokens(response)

        # Update statistics
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._call_count += 1

        return response

    def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        Synchronous generate (delegates to underlying LLM).

        Args:
            messages: List of message dicts.
            **kwargs: Additional arguments.

        Returns:
            Generated response string.
        """
        # Inject default timeout if not present in kwargs
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self._default_timeout

        # Count input tokens
        from mcpevolve.agent.react_postprocess import count_tokens
        input_text = " ".join([msg.get("content", "") for msg in messages])
        input_tokens = count_tokens(input_text)

        # Call underlying LLM
        response = self._llm.generate(messages, **kwargs)

        # Count output tokens
        output_tokens = count_tokens(response)

        # Update statistics
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._call_count += 1

        return response

    def get_token_usage(self) -> Dict[str, int]:
        """
        Get token usage statistics.

        Returns:
            Dictionary with input_tokens, output_tokens, total_tokens, and call_count.
        """
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
            "call_count": self._call_count
        }

    def reset_token_usage(self):
        """Reset token usage statistics."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0

    def __getattr__(self, name):
        """Delegate all other attributes to the underlying LLM."""
        return getattr(self._llm, name)
