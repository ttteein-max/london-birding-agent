import type { StateView } from "../api/contracts";
import { humanise } from "../utils";

type Decisions = NonNullable<StateView["hitl"]["applied_decisions"]>;
type Decision = Decisions[number];

interface Props {
  decisions?: Decisions;
  emptyMessage?: string;
}

function decisionCopy(decision: Decision): { title: string; detail: string } {
  if (decision.kind === "location_correction") {
    return {
      title: "Confirmed a London location",
      detail: "A verified place choice was applied without exposing raw coordinates.",
    };
  }
  if (decision.kind === "request_clarification") {
    return {
      title: "Clarified the expedition request",
      detail: (decision.changed_fields ?? []).length > 0
        ? `Changed: ${(decision.changed_fields ?? []).map(humanise).join(", ")}.`
        : "Validated missing or contradictory request fields.",
    };
  }
  if (decision.kind === "bird_input_correction") {
    return {
      title: "Corrected the bird input",
      detail: decision.bird_input ?? "The corrected bird name was sent back through taxonomy resolution.",
    };
  }
  if (decision.kind === "taxon_selection" || decision.kind === "related_taxon_selection") {
    const selected = decision.selected_taxon_name
      ?? (decision.accepted_taxon_key ? `GBIF taxon ${decision.accepted_taxon_key}` : "a validated taxon");
    return {
      title: decision.kind === "related_taxon_selection"
        ? `Changed target taxon to ${selected}`
        : `Selected ${selected}`,
      detail: decision.kind === "related_taxon_selection"
        ? `${decision.relation_level ? `${humanise(decision.relation_level)} relation. ` : ""}Related does not mean ecologically interchangeable.`
        : "Selected from deterministic GBIF candidates.",
    };
  }
  if (decision.kind === "actionable_tradeoff") {
    switch (decision.option) {
      case "consider_related_taxa":
        return {
          title: "Requested related-taxon alternatives",
          detail: "Opened another HITL selection step; this choice alone did not change the species.",
        };
      case "widen_seasonal_window":
        return {
          title: `Widened seasonal window to ±${decision.seasonal_window_radius_months ?? "?"} months`,
          detail: "Invalidated occurrence and public-site evidence, then re-ran them.",
        };
      case "expand_search_radius":
        return {
          title: `Expanded site search to ${decision.search_radius_km ?? "?"} km`,
          detail: "Invalidated public-site evidence and repeated site grounding.",
        };
      case "keep_constraints_accept_low_confidence":
        return {
          title: "Accepted the low-confidence result",
          detail: "Kept the constraints and allowed an explicitly non-recommendation outcome.",
        };
      case "accept_context_only":
        return {
          title: "Accepted contextual sites only",
          detail: "Continued without promoting contextual places to recommendations.",
        };
      case "continue_with_weather_acknowledgement":
        return {
          title: "Continued with weather acknowledged",
          detail: "Accepted that exact-date weather was unavailable.",
        };
      case "accept_uncertain_access":
        return {
          title: "Accepted uncertain access",
          detail: "Kept the access limitation explicit in the result.",
        };
      case "revise_rain_preference":
        return {
          title: "Removed the rain preference",
          detail: "Updated the request and resumed deterministic validation.",
        };
      default:
        return {
          title: humanise(decision.option ?? "Actionable trade-off"),
          detail: "Applied a validated human decision.",
        };
    }
  }
  return {
    title: humanise(decision.kind),
    detail: "Applied a validated human decision.",
  };
}

export function DecisionHistory({ decisions, emptyMessage = "No HITL choices had been applied by this checkpoint." }: Props) {
  const applied = decisions ?? [];
  if (applied.length === 0) return <p className="empty-copy">{emptyMessage}</p>;
  return (
    <ol className="decision-history-list" aria-label="Applied HITL decision history">
      {applied.map((decision, index) => {
        const copy = decisionCopy(decision);
        return (
          <li key={`${decision.kind}-${decision.option ?? "selection"}-${index}`}>
            <span className="decision-number">{String(index + 1).padStart(2, "0")}</span>
            <div><strong>{copy.title}</strong><span>{copy.detail}</span></div>
            <em>{decision.kind === "actionable_tradeoff" ? "Trade-off" : "HITL"}</em>
          </li>
        );
      })}
    </ol>
  );
}
