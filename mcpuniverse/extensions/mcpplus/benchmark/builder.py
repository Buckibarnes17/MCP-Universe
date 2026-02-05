"""
Extended Workflow Builder with Wrapper Support

EXISTING (MCPUniverse WorkflowBuilder):
- Parses YAML configs with 3 kinds: llm, agent, workflow
- Builds dependency graph and instantiates components
- Uses standard MCPManager for MCP protocol

THIS IMPLEMENTATION:
- Extends to support 4th kind: wrapper (for PostProcessAgent config)
- Pre-parses wrapper configs and builds MCPWrapperManager when enabled
- Tracks agent-to-wrapper mappings and filters wrapper configs before passing to parent
- Provides lookup methods to get wrapper config for specific agents
"""
from typing import List, Literal, Dict, Any, Optional

from pydantic import BaseModel, Field
from mcpuniverse.workflows.builder import WorkflowBuilder as BaseWorkflowBuilder, WorkflowConfig as BaseWorkflowConfig
from mcpuniverse.mcp.manager import MCPManager
from mcpuniverse.llm.base import BaseLLM
from mcpuniverse.agent.base import Executor
from mcpuniverse.common.context import Context
from mcpuniverse.common.logger import get_logger

from mcpevolve.mcp.wrapper_manager import MCPWrapperManager, WrapperConfig
from mcpevolve.llm import TokenTrackingLLM


class WrapperSpec(BaseModel):
    """
    Wrapper specification for post-processing.

    Attributes:
        name (str): The unique name of the wrapper configuration.
        enabled (bool): Enable/disable wrapper functionality.
        token_threshold (int): Minimum token count to trigger post-processing.
        use_agent_llm (bool): Use the same LLM as the main agent.
        post_process_llm (Dict, optional): Separate LLM config for post-processor.
        enable_memory (bool): Enable session memory for code reuse.
        execution_timeout (int): Max seconds for filter code execution.
        max_iterations (int): Maximum iterations for post-processor refinement.
        post_processor_type (str): Type of post-processor agent:
            - "extract": Direct extraction only (no code)
            - "extract_and_code": Intelligently chooses direct extraction or code
            - "code_only": Always generates code
            - "dual": Generates both extraction AND code in one call (cost-optimized)
            - "basic": Legacy single-shot code generation
        enable_reflection (bool): Enable LLM-based reflection on output quality.
        max_tool_output_chars (Optional[int]): Maximum characters of tool output to show to LLM.
            If None or 0, shows entire output. Default is 2000.
        expected_info_prompt_file (Optional[str]): Path to custom expected_info prompt file.
        post_processor_prompt_file (Optional[str]): Path to custom prompt file for the post-processor agent.
            If not provided, uses the agent's default built-in prompt. This is used for the main
            extraction/code generation prompt (e.g., DUAL_EXTRACTION_PROMPT, COMBINED_EXTRACTION_PROMPT,
            FILTER_CODE_GENERATION_PROMPT, DIRECT_EXTRACTION_PROMPT).
        use_simple_prompt (bool): For "extract" type only - use simplified extraction prompt (True)
            or ReAct-style prompt with JSON output (False). Default is True.
    """
    name: str
    enabled: bool = False
    token_threshold: int = 500
    use_agent_llm: bool = True
    post_process_llm: Optional[Dict] = None
    enable_memory: bool = True
    execution_timeout: int = 10
    max_iterations: int = 3
    post_processor_type: str = "react"
    enable_reflection: bool = True
    max_tool_output_chars: Optional[int] = 2000
    expected_info_prompt_file: Optional[str] = None
    post_processor_prompt_file: Optional[str] = None
    use_simple_prompt: bool = True


class ExtendedWorkflowConfig(BaseModel):
    """
    Extended workflow configuration that supports wrapper configs.

    Attributes:
        kind (Literal): The kind of component being configured.
        spec (WrapperSpec | dict): The specification for the component.
    """
    kind: Literal["llm", "agent", "workflow", "wrapper"]
    spec: WrapperSpec | dict


class WorkflowBuilderWithWrapper(BaseWorkflowBuilder):
    """
    Extended WorkflowBuilder that supports wrapper configurations.

    This class extends MCPUniverse's WorkflowBuilder to:
    1. Parse "wrapper" kind configurations from YAML
    2. Build MCPWrapperManager when wrapper is configured
    3. Inject wrapper configs into agents that reference them
    """

    def __init__(
        self,
        mcp_manager: Optional[MCPManager] = None,
        config: str | dict | List[dict] = None,
        context: Optional[Context] = None
    ):
        """
        Initialize the extended workflow builder.

        Args:
            mcp_manager (MCPManager, optional): An MCP manager. If None and wrapper is configured,
                                                MCPWrapperManager will be created.
            config (str | dict | List[dict]): Configuration source.
            context (Context, optional): Context information.
        """
        self._context = context if context else Context()
        self._wrapper_configs: Dict[str, WrapperConfig] = {}
        self._agent_wrapper_mapping: Dict[str, str] = {}
        self._wrapper_llm_refs: Dict[str, str] = {}  # Track LLM references in wrapper configs
        self._logger = get_logger(self.__class__.__name__)

        # Parse wrapper configs before initializing base class
        self._parse_wrapper_configs(config)

        # Determine which MCP manager to use
        if mcp_manager is None:
            if self._wrapper_configs:
                # Use MCPWrapperManager if wrapper is configured
                # We'll use the first wrapper config as default
                default_wrapper = next(iter(self._wrapper_configs.values()))
                mcp_manager = MCPWrapperManager(
                    wrapper_config=default_wrapper,
                    context=self._context
                )
            else:
                # Use standard MCPManager
                mcp_manager = MCPManager(context=self._context)

        self._mcp_manager = mcp_manager

        # Filter out wrapper configs before passing to base class
        filtered_config = self._filter_wrapper_configs(config)

        # Initialize base class
        super().__init__(mcp_manager=mcp_manager, config=filtered_config)

    def _parse_wrapper_configs(self, config: str | dict | List[dict]):
        """
        Parse wrapper configurations from the config.

        Args:
            config (str | dict | List[dict]): Configuration source.
        """
        if not config:
            return

        # Load as extended configs
        import yaml
        if isinstance(config, str):
            with open(config, "r", encoding="utf-8") as f:
                objects = yaml.safe_load_all(f)
                if isinstance(objects, dict):
                    objects = [objects]
                configs = [ExtendedWorkflowConfig.model_validate(o) for o in objects]
        elif isinstance(config, dict):
            configs = [ExtendedWorkflowConfig.model_validate(config)]
        else:
            configs = [ExtendedWorkflowConfig.model_validate(o) for o in config]

        # Extract wrapper configs and agent-wrapper mappings
        for conf in configs:
            if conf.kind == "wrapper":
                wrapper_spec = WrapperSpec.model_validate(conf.spec)
                self._wrapper_configs[wrapper_spec.name] = WrapperConfig(
                    enabled=wrapper_spec.enabled,
                    token_threshold=wrapper_spec.token_threshold,
                    use_agent_llm=wrapper_spec.use_agent_llm,
                    post_process_llm=wrapper_spec.post_process_llm,
                    enable_memory=wrapper_spec.enable_memory,
                    execution_timeout=wrapper_spec.execution_timeout,
                    max_iterations=wrapper_spec.max_iterations,
                    post_processor_type=wrapper_spec.post_processor_type,
                    enable_reflection=wrapper_spec.enable_reflection,
                    max_tool_output_chars=wrapper_spec.max_tool_output_chars,
                    expected_info_prompt_file=wrapper_spec.expected_info_prompt_file,
                    post_processor_prompt_file=wrapper_spec.post_processor_prompt_file,
                    use_simple_prompt=wrapper_spec.use_simple_prompt
                )
                # Track LLM reference if post_process_llm is specified
                if wrapper_spec.post_process_llm and 'llm' in wrapper_spec.post_process_llm:
                    self._wrapper_llm_refs[wrapper_spec.name] = wrapper_spec.post_process_llm['llm']
            elif conf.kind in ["agent", "workflow"]:
                # Check if agent references a wrapper
                spec_dict = conf.spec if isinstance(conf.spec, dict) else conf.spec.model_dump()
                agent_name = spec_dict.get("name")
                agent_config = spec_dict.get("config", {})
                wrapper_ref = agent_config.get("wrapper")
                if wrapper_ref:
                    self._agent_wrapper_mapping[agent_name] = wrapper_ref

    def _filter_wrapper_configs(self, config: str | dict | List[dict]) -> str | dict | List[dict]:
        """
        Filter out wrapper configurations from config before passing to base class.

        Args:
            config (str | dict | List[dict]): Original configuration.

        Returns:
            Filtered configuration without wrapper kinds.
        """
        if isinstance(config, str):
            # For file paths, we need to load, filter, and pass as list
            import yaml
            with open(config, "r", encoding="utf-8") as f:
                objects = list(yaml.safe_load_all(f))
            filtered = [obj for obj in objects if obj.get("kind") != "wrapper"]
            # Also remove wrapper references from agent configs
            for obj in filtered:
                if obj.get("kind") in ["agent", "workflow"]:
                    if "wrapper" in obj.get("spec", {}).get("config", {}):
                        del obj["spec"]["config"]["wrapper"]
            return filtered
        elif isinstance(config, dict):
            if config.get("kind") == "wrapper":
                return []
            return config
        else:  # list
            filtered = [obj for obj in config if obj.get("kind") != "wrapper"]
            # Remove wrapper references
            for obj in filtered:
                if obj.get("kind") in ["agent", "workflow"]:
                    if "wrapper" in obj.get("spec", {}).get("config", {}):
                        del obj["spec"]["config"]["wrapper"]
            return filtered

    def get_wrapper_config(self, wrapper_name: str) -> Optional[WrapperConfig]:
        """
        Get a wrapper configuration by name.

        Args:
            wrapper_name (str): Name of the wrapper configuration.

        Returns:
            WrapperConfig if found, None otherwise.
        """
        return self._wrapper_configs.get(wrapper_name)

    def get_agent_wrapper(self, agent_name: str) -> Optional[WrapperConfig]:
        """
        Get the wrapper configuration for an agent.

        Args:
            agent_name (str): Name of the agent.

        Returns:
            WrapperConfig if agent has a wrapper, None otherwise.
        """
        wrapper_name = self._agent_wrapper_mapping.get(agent_name)
        if wrapper_name:
            return self._wrapper_configs.get(wrapper_name)
        return None

    def build(
            self,
            components: Optional[Dict[str, BaseLLM | Executor]] = None,
            project_id: Optional[str] = "local_test"
    ):
        """
        Build the agent workflow with wrapper LLM resolution and token tracking.

        This method extends the parent build() to:
        1. Build all components normally
        2. Wrap all LLMs with token tracking
        3. Resolve LLM references in wrapper configs
        4. Set the LLM on MCPWrapperManager if needed

        Args:
            components (Dict, optional): Pre-built components to use.
            project_id (str, optional): The project ID for this workflow.
        """
        # Call parent build
        super().build(components=components, project_id=project_id)

        # Wrap all LLMs with token tracking
        # TokenTrackingLLM will read timeout from MCPEVOLVE_LLM_TIMEOUT env var or default to 120
        if hasattr(self, '_components') and self._components:
            for name, component in list(self._components.items()):
                if isinstance(component, BaseLLM) and not isinstance(component, TokenTrackingLLM):
                    wrapped_llm = TokenTrackingLLM(component)
                    self._components[name] = wrapped_llm
                    self._logger.info("Wrapped LLM '%s' with token tracking (timeout: %ds)",
                                      name, wrapped_llm._default_timeout)

        # Resolve wrapper LLM references if MCPWrapperManager is used
        # EXISTING: WorkflowBuilder doesn't know about wrappers
        # THIS IMPLEMENTATION:
        #   - If use_agent_llm=False: Set the post-processor LLM from post_process_llm.llm reference
        #   - If use_agent_llm=True: Don't set here, agent will set its own LLM later
        if isinstance(self._mcp_manager, MCPWrapperManager):
            for wrapper_name, llm_ref in self._wrapper_llm_refs.items():
                wrapper_config = self._wrapper_configs.get(wrapper_name)
                if wrapper_config and not wrapper_config.use_agent_llm:
                    # Only set if using separate post-processor LLM
                    if llm_ref in self._name2object:
                        llm = self._name2object[llm_ref]
                        self._mcp_manager.set_llm(llm)
                        self._logger.info(
                            "Set separate post-processor LLM '%s' for wrapper '%s'",
                            llm_ref, wrapper_name
                        )
                        break  # Only need to set once for the manager

    def update_mcp_manager_wrapper(self, agent_name: str):
        """
        Update the MCP manager's wrapper configuration for a specific agent.

        Args:
            agent_name (str): Name of the agent to update wrapper for.
        """
        if isinstance(self._mcp_manager, MCPWrapperManager):
            wrapper_config = self.get_agent_wrapper(agent_name)
            if wrapper_config:
                self._mcp_manager.update_wrapper_config(wrapper_config)
