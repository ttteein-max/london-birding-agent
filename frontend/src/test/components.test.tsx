import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentTrace } from "../components/AgentTrace";
import { AsyncState } from "../components/AsyncState";
import { EvidencePanel } from "../components/EvidencePanel";
import { HitlPanel } from "../components/HitlPanel";
import { PlanPanel } from "../components/PlanPanel";
import { RequestComposer } from "../components/RequestComposer";
import { RunSidebar } from "../components/RunSidebar";
import { SelectedRunRequest } from "../components/SelectedRunRequest";
import { StateInspector } from "../components/StateInspector";
import { TimeTravelPanel } from "../components/TimeTravelPanel";
import type { FinalPlanView, PendingDecisionView, RunDetail } from "../api/contracts";
import { comparisonFixture, evidenceFixture, historyFixture, planFixture, routePlanFixture, stateFixture, walkingPlanFixture, workflowTopologyFixture } from "./fixtures";

describe("field notebook cards", () => {
  it("renders plan, evidence, constraints, provenance, and exact-date weather", () => {
    render(<><PlanPanel plan={planFixture} /><EvidencePanel evidence={evidenceFixture} /></>);
    expect(screen.getByRole("heading", { name: "Evidence-grounded candidate sites" })).toBeInTheDocument();
    expect(screen.getByText("Evidence Garden")).toBeInTheDocument();
    expect(screen.getByText(/Context only — not a recommendation/)).toBeInTheDocument();
    expect(screen.getByText("24,804")).toBeInTheDocument();
    expect(screen.getByText("GBIF data funnel")).toBeInTheDocument();
    expect(screen.getByLabelText("Strong evidence gate checklist")).toHaveTextContent("227 / 50 min");
    expect(screen.getByText(/12 cells with at least 3 eligible records/)).toBeInTheDocument();
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

  it("renders the nested validated itinerary without creating a second final plan", () => {
    render(<PlanPanel plan={routePlanFixture} />);
    expect(screen.getByRole("heading", { name: "Validated journey itinerary" })).toBeInTheDocument();
    expect(screen.getByText(/180-minute total outing/)).toBeInTheDocument();
    expect(screen.getByText("Main gate · Explicit Public access evidence")).toBeInTheDocument();
    expect(screen.getByText("9.13 km")).toBeInTheDocument();
    expect(screen.getByText("58.2 min")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Walking route elevation profile/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Walking route constraints")).toHaveTextContent("Pass · Maximum Walking Distance");
    expect(screen.getByText(/fixture-openrouteservice · foot-walking/)).toBeInTheDocument();
  });

  it("does not mislabel a legacy round-trip duration as one-way travel", () => {
    const legacyPlan: FinalPlanView = {
      ...routePlanFixture,
      duration_hours: 2,
      itinerary_summary: null,
      walking_plan: {
        ...walkingPlanFixture,
        historical_execution: true,
        routing_policy_note: "Current live runs use TfL public transport plus walking.",
        selection_rationale: null,
        outbound_travel_duration_minutes: null,
        return_travel_duration_minutes: null,
        total_travel_duration_minutes: null,
        walking_duration_minutes: 85.5,
        expedition_duration_minutes: 120,
        remaining_field_time_minutes: 34.5,
      },
    };

    render(<PlanPanel plan={legacyPlan} />);

    expect(screen.getByRole("heading", { name: "Expedition overview" })).toBeInTheDocument();
    expect(screen.getByText(/85.5 minutes is the total return-journey travel time, not the outbound leg alone/)).toBeInTheDocument();
    expect(screen.getByText("Historical walking-only")).toBeInTheDocument();
    expect(screen.getAllByText("Not separately recorded")).toHaveLength(2);
    expect(screen.getByText(/Historical walking-only execution/)).toBeInTheDocument();
    expect(screen.getByText(/immutable historical route passed the constraints/)).toBeInTheDocument();
  });

  it("shows a repeated return path once without dropping its validated time", () => {
    render(<PlanPanel plan={{
      ...routePlanFixture,
      walking_plan: {
        ...walkingPlanFixture,
        journey_segments: [
          {
            segment_id: "outbound-walk",
            direction: "outbound",
            sequence: 0,
            mode: "walking",
            instruction: "Walk to the main gate",
            duration_minutes: 60.8,
          },
          {
            segment_id: "return-walk",
            direction: "return",
            sequence: 0,
            mode: "walking",
            instruction: "Walk back from the main gate",
            duration_minutes: 61,
          },
        ],
      },
    }} />);

    expect(screen.getByText(/Walk to the main gate/)).toBeInTheDocument();
    expect(screen.queryByText(/Walk back from the main gate/)).not.toBeInTheDocument();
    expect(screen.getByText("61 min")).toBeInTheDocument();
    expect(screen.getByText(/directions are shown only once/)).toBeInTheDocument();
  });

  it("keeps a Phase 4 plan readable without a walking plan", () => {
    render(<PlanPanel plan={planFixture} />);
    expect(screen.getByText(/historical plan predates validated walking routes/)).toBeInTheDocument();
  });

  it("renders a typed routing-provider failure without inventing a route", () => {
    render(<PlanPanel plan={{
      ...routePlanFixture,
      walking_plan: {
        ...walkingPlanFixture,
        status: "source_unavailable",
        limitations: ["The configured live routing provider was unavailable."],
      },
    }} />);
    expect(screen.getByText(/No journey was fabricated/)).toHaveTextContent(
      "Source Unavailable",
    );
    expect(screen.getByText("The configured live routing provider was unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("9.13 km")).not.toBeInTheDocument();
  });
});

describe("typed human decisions", () => {
  it("does not present an unevaluated taxonomy preview as zero evidence", () => {
    const candidate = {
      accepted_taxon_key: 2489281,
      common_name: "Lesser Ground-robin",
      scientific_name: "Amalocichla incerta",
      canonical_name: "Amalocichla incerta",
      rank: "SPECIES",
      taxonomic_status: "ACCEPTED",
      resolution_method: "gbif_search",
    };
    const decision: PendingDecisionView = {
      kind: "taxon_selection",
      question: "Which candidate?",
      checkpoint_id: "checkpoint-taxonomy-preview",
      branch_id: "branch-one",
      execution_id: "execution-one",
      candidates: [
        {
          ...candidate,
          evidence_preview: {
            status: "evaluated",
            retained_count: 0,
            dataset_count: 0,
            source_status: "available",
          },
        },
        {
          ...candidate,
          accepted_taxon_key: 2489282,
          common_name: "Budget-limited candidate",
          evidence_preview: {
            status: "not_evaluated_budget",
            retained_count: null,
            dataset_count: null,
            source_status: "not_evaluated_budget",
          },
        },
      ],
      options: [],
      validation_errors: [],
    };

    render(<HitlPanel decision={decision} busy={false} onResume={vi.fn()} />);

    expect(screen.getByText("Evaluated · 0 retained · 0 datasets")).toBeInTheDocument();
    expect(screen.getByText("Not evaluated · preview budget exhausted")).toBeInTheDocument();
    expect(screen.queryByText(/Not Evaluated Budget · 0 retained/)).not.toBeInTheDocument();
  });

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

  it("submits only the typed route limit change", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "route_tradeoff",
      question: "The computed route exceeds the supplied walking limit.",
      checkpoint_id: "checkpoint-route",
      branch_id: "branch-one",
      execution_id: "execution-one",
      options: [{ option: "increase_maximum_walking_distance", minimum_walking_distance_km: 9.13 }],
      candidates: [],
      validation_errors: [],
    };
    render(<HitlPanel decision={decision} busy={false} onResume={onResume} />);
    const input = screen.getByLabelText("Maximum full-excursion walking distance (km)");
    await userEvent.clear(input);
    await userEvent.type(input, "10");
    await userEvent.click(screen.getByRole("button", { name: "Recalculate routes" }));
    await waitFor(() => expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-route",
      decision: {
        kind: "route_tradeoff",
        option: "increase_maximum_walking_distance",
        maximum_walking_distance_km: 10,
      },
    }));
  });

  it("shows the safe partial parse and submits only the missing field", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "request_clarification",
      question: "Please correct the request.",
      checkpoint_id: "checkpoint-clarification",
      branch_id: "branch-one",
      execution_id: "execution-one",
      options: [],
      candidates: [],
      validation_errors: ["Missing or contradictory field: target_local_date."],
      parsed_draft: {
        bird_input: "Yellow-browed Warbler",
        postcode: null,
        location_query: "Rainham Marshes",
        has_explicit_start_point: false,
        target_local_date: null,
        duration_hours: 3,
      },
    };
    render(<HitlPanel decision={decision} busy={false} onResume={onResume} />);
    expect(screen.getByLabelText("Recognised request details")).toHaveTextContent("Rainham Marshes");
    expect(screen.getByLabelText(/Named London place/)).toHaveValue("Rainham Marshes");
    expect(screen.getByLabelText("Bird name")).toHaveValue("Yellow-browed Warbler");
    expect(screen.getByLabelText(/Total outing hours/)).toHaveValue(3);
    await userEvent.type(screen.getByLabelText("Target date"), "2026-09-12");
    await userEvent.click(screen.getByRole("button", { name: "Apply corrections" }));
    expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-clarification",
      decision: { kind: "request_clarification", updates: { target_local_date: "2026-09-12" } },
    });
  });

  it("submits an allow-listed geocoder candidate without exposing coordinates", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const decision: PendingDecisionView = {
      kind: "location_correction",
      question: "Select the intended place.",
      status: "human_selection_required",
      checkpoint_id: "checkpoint-location",
      branch_id: "branch-one",
      execution_id: "execution-one",
      options: [],
      candidates: [],
      validation_errors: [],
      location_candidates: [{
        candidate_id: "place-1",
        label: "Kensal Road",
        locality: "North Kensington",
        administrative_district: "Royal Borough of Kensington and Chelsea",
        postcode: "W10 5DD",
        category: "highway",
        place_type: "unclassified",
      }],
    };
    const { container } = render(
      <HitlPanel decision={decision} busy={false} onResume={onResume} />,
    );
    expect(screen.getByText(/© OpenStreetMap contributors/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/latitude|longitude/i);
    await userEvent.click(screen.getByRole("button", { name: "Use Kensal Road W10 5DD" }));
    expect(onResume).toHaveBeenCalledWith({
      checkpoint_id: "checkpoint-location",
      decision: { kind: "location_correction", candidate_id: "place-1" },
    });
  });
});

describe("trace and time travel", () => {
  it("renders nested span duration without adding it to the node", () => {
    render(<AgentTrace topology={workflowTopologyFixture} history={historyFixture} activeExecutionId="execution-original" selectedExecutionId="execution-original" connection="connected" onCheckpoint={vi.fn()} onInspectCheckpoint={vi.fn()} onExecutionChange={vi.fn()} events={[
      { run_id: "op", sequence: 1, event_type: "node_completed", node_id: "evidence_agent", timestamp: "2026-09-02T00:00:00Z", duration_ms: 1200, payload: {} },
      { run_id: "op", sequence: 2, event_type: "model_completed", node_id: "evidence_agent", parent_span_id: "node", timestamp: "2026-09-02T00:00:01Z", duration_ms: 450, payload: { model: "scripted" } },
    ]} />);
    expect(screen.getByText("1.20 s")).toBeInTheDocument();
    expect(screen.getByText("450 ms")).toBeInTheDocument();
    expect(screen.getByText(/nested inside node wall time/)).toBeInTheDocument();
    expect(screen.getByLabelText("Complete LangGraph topology with execution state")).toBeInTheDocument();
  });

  it("marks a waiting HITL node amber and exposes a failed node reason", () => {
    const waitingHistory = {
      ...historyFixture,
      checkpoints: [
        ...historyFixture.checkpoints,
        {
          ...historyFixture.checkpoints[1],
          checkpoint_id: "checkpoint-waiting",
          next_nodes: ["actionable_tradeoff_interrupt"],
          interrupt_kind: "actionable_tradeoff",
        },
      ],
      executions: historyFixture.executions.map((execution) => ({
        ...execution,
        head_checkpoint_id: "checkpoint-waiting",
        terminal_status: undefined,
        interrupt_kind: "actionable_tradeoff",
      })),
    };
    const { rerender } = render(
      <AgentTrace
        topology={workflowTopologyFixture}
        history={waitingHistory}
        activeExecutionId="execution-original"
        selectedExecutionId="execution-original"
        connection="closed"
        onCheckpoint={vi.fn()}
        onInspectCheckpoint={vi.fn()}
        onExecutionChange={vi.fn()}
        events={[]}
      />,
    );
    expect(screen.getByLabelText("Actionable trade-off: Waiting")).toHaveClass("node-status-waiting");

    rerender(
      <AgentTrace
        topology={workflowTopologyFixture}
        history={null}
        activeExecutionId={null}
        selectedExecutionId={null}
        connection="closed"
        onCheckpoint={vi.fn()}
        onInspectCheckpoint={vi.fn()}
        onExecutionChange={vi.fn()}
        events={[{
          run_id: "failed-op",
          sequence: 1,
          event_type: "node_failed",
          node_id: "evidence_agent",
          timestamp: "2026-09-02T00:00:00Z",
          payload: { error_type: "RuntimeError" },
        }]}
      />,
    );
    expect(screen.getByLabelText("Evidence agent: Failed")).toHaveTextContent("Stopped: RuntimeError");
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
  it("shows the exact natural-language request for the selected local run", () => {
    const detail: RunDetail = {
      run: {
        thread_id: "expedition-history",
        status: "completed",
        data_mode: "fixture",
        model_mode: "scripted",
        created_at: "2026-09-03T04:15:31Z",
        updated_at: "2026-09-03T04:16:41Z",
      },
      submitted_request: {
        text: "Plan a two-hour expedition from Kensal Road on 15 June 2026 to look for Common woodpigeon.",
        language: "English",
        visibility: "local_only",
      },
      operations: [],
      branches: [],
      executions: [],
      pending_decisions: [],
      final_plan: null,
    };
    render(<SelectedRunRequest detail={detail} state={stateFixture} />);
    expect(screen.getByRole("heading", { name: "Natural-language request" })).toBeInTheDocument();
    expect(screen.getByText(/Plan a two-hour expedition from Kensal Road/)).toBeInTheDocument();
    expect(screen.getByLabelText("Selected run request metadata")).toHaveTextContent("expedition-history");
    expect(screen.getByText("fixture/scripted")).toBeInTheDocument();
    expect(screen.getByLabelText("HITL decisions for selected execution")).toHaveTextContent("Widened seasonal window to ±2 months");
    expect(screen.getByLabelText("HITL decisions for selected execution")).toHaveTextContent("Expanded site search to 6.9 km");
  });

  it("explains checkpoints and shows decisions accumulated by the selected state", () => {
    render(<StateInspector state={stateFixture} loading={false} onClose={vi.fn()} />);
    expect(screen.getByText(/CP means checkpoint/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "HITL decisions at this checkpoint" })).toBeInTheDocument();
    expect(screen.getByLabelText("Applied HITL decision history")).toHaveTextContent("Widened seasonal window to ±2 months");
  });

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
