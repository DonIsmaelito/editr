export interface StartSearchResponse {
  search_id: string;
  status: string;
}

export async function startFindrSearch(
  query: string,
  conversationContext: string,
  signal?: AbortSignal
): Promise<StartSearchResponse> {
  const res = await fetch("/api/findr/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      conversation_context: conversationContext,
    }),
    signal,
  });

  if (!res.ok) {
    throw new Error(`Server returned ${res.status}`);
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(
      "Expected JSON from /search but received stream. Ensure /search is the JSON start endpoint."
    );
  }

  return (await res.json()) as StartSearchResponse;
}
