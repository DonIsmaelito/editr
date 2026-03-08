export interface StartEditResponse {
  job_id: string;
  status: string;
}

export async function startViralFixEdit(
  username: string,
  platform: string,
  maxVideos: number,
  signal?: AbortSignal
): Promise<StartEditResponse> {
  const res = await fetch("/api/editr/api/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      platform,
      max_videos: maxVideos,
    }),
    signal,
  });

  if (!res.ok) {
    throw new Error(`Server returned ${res.status}`);
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error("Expected JSON from /edit but received stream.");
  }

  return (await res.json()) as StartEditResponse;
}
