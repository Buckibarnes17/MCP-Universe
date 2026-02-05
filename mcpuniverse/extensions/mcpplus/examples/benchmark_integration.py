"""
MCPPlus Benchmark Integration Example

This example demonstrates how to run MCP-Universe benchmarks with MCPPlus
post-processing enabled.

Prerequisites:
- MCP-Universe installed: pip install mcpuniverse
- Benchmark config file (see examples/configs/benchmark_with_wrapper.yaml)
- API keys set in .env file
"""
import asyncio
from mcpuniverse.benchmark.runner import BenchmarkRunner
from mcpuniverse.tracer.collectors import MemoryCollector


async def main():
    """
    Run a benchmark with MCPPlus post-processing enabled.

    The benchmark config includes wrapper configuration, which will
    automatically enable post-processing for all tool calls during the benchmark.
    """
    print("=" * 80)
    print("MCPPlus Benchmark Integration Example")
    print("=" * 80)

    # Step 1: Initialize trace collector
    print("\n1. Initializing trace collector...")
    trace_collector = MemoryCollector()
    print("   ✓ Memory collector ready")

    # Step 2: Load benchmark with wrapper config
    print("\n2. Loading benchmark configuration...")
    # This config includes wrapper configuration
    benchmark = BenchmarkRunner("examples/configs/benchmark_with_wrapper.yaml")
    print("   ✓ Benchmark loaded")
    print(f"   Agent: {benchmark.config.get('agent', 'N/A')}")
    print(f"   Tasks: {len(benchmark.config.get('tasks', []))} tasks")

    # Step 3: Run the benchmark
    print("\n3. Running benchmark with post-processing...")
    print("   This may take a few minutes depending on task complexity")
    print("-" * 80)

    try:
        results = await benchmark.run(trace_collector=trace_collector)

        print("-" * 80)
        print("\n4. Benchmark Results:")
        print(f"   Total tasks: {len(results)}")

        # Display results
        for i, result in enumerate(results, 1):
            status = "✓ PASS" if result.success else "✗ FAIL"
            print(f"   Task {i}: {status}")
            if hasattr(result, 'task_name'):
                print(f"           {result.task_name}")

        # Calculate success rate
        success_count = sum(1 for r in results if r.success)
        success_rate = (success_count / len(results)) * 100 if results else 0
        print(f"\n   Success Rate: {success_rate:.1f}% ({success_count}/{len(results)})")

    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        print("   Check:")
        print("   - Benchmark config is valid")
        print("   - MCP servers are accessible")
        print("   - API keys are configured")
        return

    # Step 5: Analyze post-processing statistics
    print("\n5. Post-Processing Statistics:")

    # Get wrapper statistics from benchmark
    if hasattr(benchmark, 'wrapper_manager'):
        stats = benchmark.wrapper_manager.get_all_postprocessor_stats()

        print(f"   Tool calls processed: {stats['tool_calls_processed']}")
        print(f"   Total iterations: {stats['total_iterations']}")
        print(f"   Original tokens: {stats['original_tokens']:,}")
        print(f"   Filtered tokens: {stats['filtered_tokens']:,}")
        print(f"   Tokens saved: {stats['total_tokens_reduced']:,}")

        if stats['original_tokens'] > 0:
            compression = (stats['total_tokens_reduced'] / stats['original_tokens']) * 100
            print(f"   Compression rate: {compression:.1f}%")

            # Estimate cost savings (assuming $0.03 per 1K tokens for GPT-4)
            cost_per_1k = 0.03
            cost_saved = (stats['total_tokens_reduced'] / 1000) * cost_per_1k
            print(f"   Estimated cost saved: ${cost_saved:.2f}")
    else:
        print("   No post-processing statistics available")
        print("   (Wrapper may not have been used)")

    # Step 6: Analyze traces
    print("\n6. Trace Analysis:")
    if results:
        # Get first task's trace
        first_result = results[0]
        if hasattr(first_result, 'task_trace_ids') and first_result.task_trace_ids:
            first_task = list(first_result.task_trace_ids.keys())[0]
            trace_id = first_result.task_trace_ids[first_task]
            trace_records = trace_collector.get(trace_id)

            print(f"   Trace records for first task: {len(trace_records)}")
            print(f"   Trace ID: {trace_id}")

            # Count different record types
            record_types = {}
            for record in trace_records:
                record_type = record.get('type', 'unknown')
                record_types[record_type] = record_types.get(record_type, 0) + 1

            print("   Record types:")
            for rtype, count in record_types.items():
                print(f"     - {rtype}: {count}")
        else:
            print("   No trace IDs available")
    else:
        print("   No results to analyze")

    # Step 7: Generate report (optional)
    print("\n7. Generating Report:")
    try:
        from mcpuniverse.benchmark.report import BenchmarkReport

        report = BenchmarkReport(benchmark, trace_collector=trace_collector)
        report_path = report.dump()
        print(f"   ✓ Report saved to: {report_path}")
    except Exception as e:
        print(f"   ⚠️  Could not generate report: {e}")

    print("\n" + "=" * 80)
    print("Benchmark complete!")
    print("\nKey Takeaways:")
    print("- MCPPlus automatically post-processes tool outputs during benchmarks")
    print("- Token savings are tracked and included in statistics")
    print("- No changes needed to benchmark tasks or agent code")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
