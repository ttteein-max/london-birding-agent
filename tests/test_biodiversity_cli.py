"""Terminal renderer regressions for Phase 2 graph updates."""

from langchain.messages import AIMessage

from scripts.run_biodiversity_agent import _print_updates


class ParallelUpdateGraph:
    def stream(self, graph_input, config, *, stream_mode, version):
        del graph_input, config, stream_mode, version
        yield {
            "data": {
                "evidence_tools": [
                    {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "search_occurrences",
                                        "args": {},
                                        "id": "parallel-1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    },
                    {
                        "occurrence_evidence": {
                            "outcome": "strong_map_evidence"
                        }
                    },
                    {
                        "grounding_errors": [
                            "A diagnostic grounding failure."
                        ]
                    },
                ]
            }
        }


def test_cli_renders_parallel_toolnode_update_fragments(capsys) -> None:
    _print_updates(ParallelUpdateGraph(), {}, {})
    output = capsys.readouterr().out
    assert "NODE evidence_tools" in output
    assert "TOOL CALL search_occurrences args={}" in output
    assert "EVIDENCE occurrence_evidence status=strong_map_evidence" in output
    assert "GROUNDING ERROR A diagnostic grounding failure." in output
