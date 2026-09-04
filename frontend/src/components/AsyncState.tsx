interface Props {
  kind: "loading" | "empty" | "error";
  title: string;
  detail: string;
}

export function AsyncState({ kind, title, detail }: Props) {
  return (
    <div className={`async-state async-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span className="async-symbol" aria-hidden="true">
        {kind === "loading" ? "◌" : kind === "empty" ? "◇" : "!"}
      </span>
      <div><strong>{title}</strong><p>{detail}</p></div>
    </div>
  );
}
