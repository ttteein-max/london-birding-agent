import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentTrace } from "../components/AgentTrace";
import { AsyncState } from "../components/AsyncState";
import { EvidencePanel } from "../components/EvidencePanel";
import { HitlPanel } from "../components/HitlPanel";
import { PlanPanel } from "../components/PlanPanel";
import { RequestComposer } from "../components/RequestComposer";
import { RunSidebar } from "../components/RunSidebar";
import { TimeTravelPanel } from "../components/TimeTravelPanel";
import type { FinalPlanView, PendingDecisionView } from "../api/contracts";
import { comparisonFixture, evidenceFixture, historyFixture, planFixture } from "./fixtures";

describe("field notebook cards", () => {
  it("renders plan, evidence, constraints, provenance, and exact-date weather", () => {
    render(<><PlanPanel plan={planFixture} /><EvidencePanel evidence={evidenceFixture} /></>);
    expect(screen.getByRole("heading", { name: "Evidence-grounded candidate sites" })).toBeInTheDocument();
    expect(screen.getByText("Evidence Garden")).toBeInTheDocument();
    expect(screen.getByText(/Context only — not a recommendation/)).toBeInTheDocument();
    expect(screen.getByText("24,804")).toBeInTheDocument();
    expect(screen.getByText("Server match count is not abundance or a population estimate.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Constraint ledger" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Daily weather context" })).toBeInTheDocument();
    expect(screen.getByLabelText("Exact-date daily weather")).toHaveTextContent("22°");
    expect(screen.getByText("GBIF.org and contributing datasets")).toBeInTheDocument();
  });

  it.each([
    ["strong", "Strong evidence"],
    ["limited", "Limited evidence"],
    ["insufficient", "Insufficient evidence"],
    ["source_failure", "Source Failure evidence"],
  ] as const)("renders the %s evidence state", (status, title) => {
    render(<EvidencePanel evidence={{ ...evidenceFixture, status }} />);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  });

  it("never renders a candidate recommendation for a low-confidence plan", () => {
    const lowPlan: FinalPlanView = {
      ...planFixture,
      status: "context_only",
      recommended_sites: [],
      evidence_gate_passed: false,
      low_confidence_accepted: true,
      low_confidence_notice: "The evidence gate did not pass; this remains a non-recommendation result.",
    };
    render(<PlanPanel plan={lowPlan} />);
    expect(screen.getByText("None at this evidence gate.")).toBeInTheDocument();
    expect(screen.queryByText("Evidence Garden")).not.toBeInTheDocument();
    expect(screen.getByText(/non-recommendation result/)).toBeInTheDocument();
  });

  it("makes deterministic fallback status explicit", () => {
    render(<PlanPanel plan={{ ...planFixture, generated_by: "deterministic_fallback" }} />);
    expect(screen.getByText("Deterministic fallback plan")).toBeInTheDocument();
  });
});

describe("typed human decisions", () => {
  it("submits an allow-listed taxonomy selection payload", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "taxon_selection",
      question: "Which candidate?",
      checkpoint_id: "checkpoint-taxonomy",
      branch_id: "branch-one",
      execution_id: "execution-one",
      candidates: [{
        accepted_taxon_key: 2489281,
        common_name: "Lesser Ground-robin",
        scientific_name: "Amalocichla incerta",
        canonical_name: "Amalocichla incerta",
        rank: "SPECIES",
        taxonomic_status: "ACCEPTED",
        resolution_method: "gbif_search",
      }],
      options: [],
      validation_errors: [],
    };
    render(<HitlPanel decision={decision} busy={false} onResume={onResume} />);
    await userEvent.click(screen.getByRole("button", { name: /Lesser Ground-robin/ }));
    expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-taxonomy",
      decision: { kind: "taxon_selection", accepted_taxon_key: 2489281 },
    });
  });

  it("submits low-evidence acceptance without editable JSON", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "actionable_tradeoff",
      question: "How should low evidence continue?",
      checkpoint_id: "checkpoint-low",
      branch_id: "branch-one",
      execution_id: "execution-one",
      options: [{ option: "keep_constraints_accept_low_confidence", relation_levels: [] }],
      candidates: [],
      validation_errors: [],
    };
    render(<HitlPanel decision={decision} busy={false} onResume={onResume} />);
    expect(screen.queryByLabelText(/JSON/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Keep Constraints Accept Low Confidence/ }));
    expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-low",
      decision: { kind: "actionable_tradeoff", option: "keep_constraints_accept_low_confidence" },
    });
  });

  it("submits only user-entered request clarification fields", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "request_clarification",
      question: "Please correct the request.",
      checkpoint_id: "checkpoint-clarification",
      branch_id: "branch-one",
      execution_id: "execution-one",
      options: [],
      candidates: [],
      validation_errors: ["Missing or contradictory field: postcode_or_start_point."],
    };
    render(<HitlPanel decision={decision} busy={false} onResume={onResume} />);
    await userEvent.type(screen.getByLabelText("London postcode"), "W10 5BN");
    await userEvent.click(screen.getByRole("button", { name: "Apply corrections" }));
    expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-clarification",
      decision: { kind: "request_clarification", updates: { postcode: "W10 5BN" } },
    });
  });
});

describe("trace and time travel", () => {
  it("renders nested span duration without adding it to the node", () => {
    render(<AgentTrace connection="connected" onCheckpoint={vi.fn()} events={[
      { run_id: "op", sequence: 1, event_type: "node_completed", node_id: "evidence_agent", timestamp: "2026-09-02T00:00:00Z", duration_ms: 1200, payload: {} },
      { run_id: "op", sequence: 2, event_type: "model_completed", node_id: "evidence_agent", parent_span_id: "node", timestamp: "2026-09-02T00:00:01Z", duration_ms: 450, payload: { model: "scripted" } },
    ]} />);
    expect(screen.getByText("1.20 s")).toBeInTheDocument();
    expect(screen.getByText("450 ms")).toBeInTheDocument();
    expect(screen.getByText(/nested inside node wall time/)).toBeInTheDocument();
  });

  it("labels identities, disables terminal replay/no-op fork, and highlights deterministic changes", async () => {
    render(<TimeTravelPanel history={historyFixture} comparison={comparisonFixture} busy={false} onInspect={vi.fn()} onLoadState={vi.fn().mockResolvedValue({
      schema_version: 1,
      thread_id: "thread-one",
      branch_id: "branch-original",
      execution_id: "execution-original",
      checkpoint_id: "checkpoint-final",
      node_id: "grounding_and_safety_checks",
      graph_step: 14,
      created_at: "2026-09-02T10:00:00Z",
      next_nodes: [],
      request: { search_radius_km: 5 },
      evidence: { safe_map_cell_count: 0, candidate_site_count: 0, contextual_site_count: 0, tool_error_count: 0 },
      plan: { recommended_site_count: 0, contextual_site_count: 0, low_confidence_accepted: false, grounding_error_count: 0 },
      hitl: { waiting: false, applied_decisions: [] },
      counters: { evidence_loop_count: 0, plan_revision_count: 0, recorded_tool_call_count: 0 },
      invalidated_evidence: [],
    })} onReplay={vi.fn()} onFork={vi.fn()} onCompare={vi.fn()} />);
    expect(screen.getByText(/Thread is the expedition/)).toBeInTheDocument();
    expect(screen.getByText("Original final")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminal · replay disabled" })).toBeDisabled();
    await screen.findByText("Current value: 5");
    await userEvent.type(screen.getByLabelText("New value"), "5");
    expect(screen.getByRole("button", { name: "Create fork" })).toBeDisabled();
    expect(screen.getByText(/choose a different value/)).toBeInTheDocument();
    expect(screen.getAllByText("Changed").length).toBeGreaterThan(0);
    expect(screen.getByText(/original final plan remains immutable/i)).toBeInTheDocument();
  });
});

describe("loading, empty, error, and accessibility states", () => {
  it("renders explicit async states", () => {
    const { rerender } = render(<AsyncState kind="empty" title="No expedition selected" detail="Start a run." />);
    expect(screen.getByRole("status")).toHaveTextContent("No expedition selected");
    rerender(<AsyncState kind="error" title="API error" detail="Retry safely." />);
    expect(screen.getByRole("alert")).toHaveTextContent("API error");
  });

  it("provides labelled request input and fixture example controls", () => {
    render(<RequestComposer
      busy={false}
      allowedModes={[
        { data_mode: "fixture", model_mode: "scripted" },
        { data_mode: "live", model_mode: "live" },
      ]}
      defaultMode={{ data_mode: "fixture", model_mode: "scripted" }}
      onSubmit={vi.fn().mockResolvedValue(undefined)}
    />);
    expect(screen.getByLabelText("Natural-language request")).toBeInTheDocument();
    expect(screen.getByLabelText("Run mode")).toHaveValue("fixture/scripted");
    expect(screen.getByRole("option", { name: "live/live" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Strong evidence" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start expedition" })).toBeInTheDocument();
  });

  it("submits the selected live/live mode", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<RequestComposer
      busy={false}
      allowedModes={[
        { data_mode: "fixture", model_mode: "scripted" },
        { data_mode: "live", model_mode: "live" },
      ]}
      defaultMode={{ data_mode: "fixture", model_mode: "scripted" }}
      onSubmit={onSubmit}
    />);
    await userEvent.selectOptions(screen.getByLabelText("Run mode"), "live/live");
    await userEvent.click(screen.getByRole("button", { name: "Start expedition" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.stringContaining("Common woodpigeon"),
      { data_mode: "live", model_mode: "live" },
    );
  });

  it("labels the persisted mode for every recent run", () => {
    render(<RunSidebar
      runs={[{
        thread_id: "expedition-live",
        status: "completed",
        data_mode: "live",
        model_mode: "live",
        created_at: "2026-09-03T04:15:31Z",
        updated_at: "2026-09-03T04:16:41Z",
      }]}
      selectedThread={null}
      detail={null}
      onSelect={vi.fn()}
    />);
    expect(screen.getByText("live/live")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expedition-live.*live\/live/i })).toBeInTheDocument();
  });
});
