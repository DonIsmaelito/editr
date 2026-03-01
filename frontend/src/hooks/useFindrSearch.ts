/**
 * useFindrSearch — React hook using Convex live subscriptions.
 *
 * Flow:
 *  1) POST /search to get search_id immediately
 *  2) Subscribe to Convex queries for search status, events, and results
 *  3) Project those live docs into chat UI state
 */

import { useCallback, useRef, useState } from "react";
import { ConvexReactClient } from "convex/react";
import { makeFunctionReference } from "convex/server";
import { startFindrSearch } from "@/lib/findr-api";

export interface VideoMoment {
  timestamp: string;
  description: string;
}

export interface VideoResult {
  videoName: string;
  videoId?: string;
  embedUrl?: string;
  moments: VideoMoment[];
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  videoResults?: VideoResult[];
  isLoading?: boolean;
  stage?: string;
  traceSteps?: TraceStep[];
  engagementQuestion?: string;
}

export type TraceKind =
  | "intent"
  | "search"
  | "chunk"
  | "rank"
  | "pinpoint"
  | "generic";

export interface TraceStep {
  id: string;
  kind: TraceKind;
  label: string;
  durationSec: number;
  active: boolean;
}

type Status =
  | "idle"
  | "loading"
  | "streaming"
  | "complete"
  | "error";

const STAGE_LABELS: Record<string, string> = {
  classifying: "Classifying your query...",
  searching: "Searching for videos...",
  processing: "Analyzing transcripts...",
  finding: "Finding exact moments...",
};

const TRACE_MESSAGES = {
  intent: "Interpreting intent from your query...",
  searchYoutube: "Searching YouTube for transcript-enabled videos...",
  searchTikTok: "Searching TikTok for relevant clips...",
  searchX: "Searching X for relevant posts...",
  searchGeneric: "Searching for relevant content...",
  chunk: "Chunking transcript into semantic segments...",
  rank: "Ranking segments by reasoning similarity...",
  pinpoint: "Pinpointing exact timestamp...",
} as const;

type SearchStatusDoc =
  | "classifying"
  | "searching"
  | "analyzing"
  | "complete"
  | "error";

type SearchEventType =
  | "status"
  | "trace"
  | "moment"
  | "clarification"
  | "done"
  | "error";

interface SearchDoc {
  status: SearchStatusDoc;
}

interface SearchEventDoc {
  eventType: SearchEventType;
  stage?: string;
  message?: string;
  createdAt: number;
}

interface SearchResultDoc {
  videoId: string;
  title: string;
  embedUrl: string;
  description?: string;
  startTime: number;
  order: number;
}

const eventsBySearchRef = makeFunctionReference<
  "query",
  { searchId: string; limit?: number },
  SearchEventDoc[]
>("events:bySearch");

const resultsBySearchRef = makeFunctionReference<
  "query",
  { searchId: string },
  SearchResultDoc[]
>("results:bySearch");

const searchGetRef = makeFunctionReference<
  "query",
  { id: string },
  SearchDoc | null
>("searches:get");

const convexUrl = process.env.NEXT_PUBLIC_CONVEX_URL;
const convexClient = convexUrl ? new ConvexReactClient(convexUrl) : null;

function getSearchLabelFromMessage(message?: string): string {
  const text = (message || "").toLowerCase();
  if (/\byoutube\b/.test(text)) {
    return TRACE_MESSAGES.searchYoutube;
  }
  if (/\btiktok\b/.test(text)) {
    return TRACE_MESSAGES.searchTikTok;
  }
  if (/\btwitter\b/.test(text) || /\bx\b/.test(text)) {
    return TRACE_MESSAGES.searchX;
  }
  return TRACE_MESSAGES.searchGeneric;
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function getEngagementQuestion(
  query: string,
  hasResults: boolean
): string {
  if (!hasResults) {
    return "What kind of videos are you looking for?";
  }

  const normalized = query.toLowerCase();

  if (
    /\b(learn|learning|understand|explain|teach|study|tutorial|course|lesson|how to)\b/.test(
      normalized
    )
  ) {
    return "What else would you like to learn today?";
  }

  if (
    /\b(review|reviews|unboxing|comparison|compare|versus|vs\.?|hands[- ]on)\b/.test(
      normalized
    )
  ) {
    return "Want to see more reviews?";
  }

  if (
    /\b(travel|vacation|trip|tour|tourist|destination|destinations|beach|resort|itinerary|city guide|places to visit)\b/.test(
      normalized
    )
  ) {
    return "Want to see more vacation spots?";
  }

  return "What other videos would you like to see next?";
}

export function useFindrSearch(
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
) {
  const [status, setStatus] = useState<Status>("idle");
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const unsubscribersRef = useRef<Array<() => void>>([]);
  const liveStateRef = useRef<{
    query: string;
    search: SearchDoc | null;
    events: SearchEventDoc[];
    results: SearchResultDoc[];
  }>({
    query: "",
    search: null,
    events: [],
    results: [],
  });
  const conversationRef = useRef("");

  const clearSubscriptions = useCallback(() => {
    for (const unsubscribe of unsubscribersRef.current) {
      unsubscribe();
    }
    unsubscribersRef.current = [];
    liveStateRef.current.search = null;
    liveStateRef.current.events = [];
    liveStateRef.current.results = [];
  }, []);

  const abortSearch = useCallback(() => {
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    clearSubscriptions();
  }, [clearSubscriptions]);

  const normalizeTraceEvent = useCallback(
    (
      event: SearchEventDoc
    ): { kind: TraceKind; label: string; createdAt: number } | null => {
    if (event.eventType === "status") {
      if (event.stage === "classifying") {
        return {
          kind: "intent",
          label: TRACE_MESSAGES.intent,
          createdAt: event.createdAt,
        };
      }
      if (event.stage === "searching") {
        return {
          kind: "search",
          label: getSearchLabelFromMessage(event.message),
          createdAt: event.createdAt,
        };
      }
      if (event.stage === "processing") {
        return {
          kind: "chunk",
          label: TRACE_MESSAGES.chunk,
          createdAt: event.createdAt,
        };
      }
      if (event.stage === "finding") {
        return {
          kind: "pinpoint",
          label: TRACE_MESSAGES.pinpoint,
          createdAt: event.createdAt,
        };
      }
      return null;
    }

    if (event.eventType === "moment") {
      return {
        kind: "pinpoint",
        label: TRACE_MESSAGES.pinpoint,
        createdAt: event.createdAt,
      };
    }

    if (event.eventType !== "trace" || !event.message) {
      return null;
    }

    const text = event.message.toLowerCase();
    if (/vector|similarity|similar|filtered|segments kept|search kept/.test(text)) {
      return {
        kind: "rank",
        label: TRACE_MESSAGES.rank,
        createdAt: event.createdAt,
      };
    }
    if (/finding exact moments|found [0-9]+ moment|timestamp|moment/.test(text)) {
      return {
        kind: "pinpoint",
        label: TRACE_MESSAGES.pinpoint,
        createdAt: event.createdAt,
      };
    }
    if (/transcript|embedded segment|cache hit|cache miss|processed transcript/.test(text)) {
      return {
        kind: "chunk",
        label: TRACE_MESSAGES.chunk,
        createdAt: event.createdAt,
      };
    }
    if (/searching youtube|searching tiktok|searching x|searching twitter|sub-query|transcript-enabled/.test(text)) {
      return {
        kind: "search",
        label: getSearchLabelFromMessage(event.message),
        createdAt: event.createdAt,
      };
    }
    if (/classif|intent|classified/.test(text)) {
      return {
        kind: "intent",
        label: TRACE_MESSAGES.intent,
        createdAt: event.createdAt,
      };
    }
    return null;
    },
    []
  );

  const computeTraceSteps = useCallback(
    (events: SearchEventDoc[], isFinal: boolean): TraceStep[] => {
      const ordered = [...events].sort((a, b) => a.createdAt - b.createdAt);
      const milestones: Array<{ kind: TraceKind; label: string; startedAt: number }> = [];
      for (const event of ordered) {
        const normalized = normalizeTraceEvent(event);
        if (!normalized) continue;
        const last = milestones[milestones.length - 1];
        if (
          last &&
          last.kind === normalized.kind &&
          last.label === normalized.label
        ) {
          continue;
        }
        milestones.push({
          kind: normalized.kind,
          label: normalized.label,
          startedAt: normalized.createdAt,
        });
      }
      const trimmed = milestones.slice(-8);
      const now = Date.now();
      return trimmed.map((step, index) => {
        const next = trimmed[index + 1];
        const endTime = next ? next.startedAt : now;
        const durationSec = Math.max(
          1,
          Math.round((endTime - step.startedAt) / 1000)
        );
        return {
          id: `${step.kind}-${step.startedAt}-${index}`,
          kind: step.kind,
          label: step.label,
          durationSec,
          active: !isFinal && index === trimmed.length - 1,
        };
      });
    },
    [normalizeTraceEvent]
  );

  const mapResults = useCallback((results: SearchResultDoc[]): VideoResult[] => {
    const sorted = [...results].sort((a, b) => {
      if (a.order !== b.order) return a.order - b.order;
      return a.startTime - b.startTime;
    });

    const byVideo = new Map<string, VideoResult>();
    for (const item of sorted) {
      const key = item.videoId || `${item.title}:${item.order}`;
      const existing = byVideo.get(key);
      const moment: VideoMoment = {
        timestamp: formatTimestamp(item.startTime || 0),
        description: item.description || "",
      };
      if (existing) {
        existing.moments.push(moment);
        continue;
      }
      byVideo.set(key, {
        videoName: item.title || "Video",
        videoId: item.videoId,
        embedUrl: item.embedUrl,
        moments: [moment],
      });
    }

    return Array.from(byVideo.values());
  }, []);

  const syncAssistantFromLiveData = useCallback(() => {
    const live = liveStateRef.current;
    const events = live.events || [];
    const results = mapResults(live.results || []);
    const lastStatusEvent = [...events]
      .reverse()
      .find((event) => event.eventType === "status");
    const stageKey = lastStatusEvent?.stage || "";
    const stageLabel =
      stageKey && STAGE_LABELS[stageKey]
        ? STAGE_LABELS[stageKey]
        : stage || "Working...";

    const errorEvent = [...events]
      .reverse()
      .find((event) => event.eventType === "error");
    const isComplete =
      live.search?.status === "complete" ||
      events.some((event) => event.eventType === "done");
    const isError = Boolean(errorEvent) || live.search?.status === "error";
    const traceSteps = computeTraceSteps(events, isComplete || isError);

    if (isError) {
      setStatus("error");
      setError(errorEvent?.message || "Something went wrong");
    } else if (isComplete) {
      setStatus("complete");
      setError(null);
    } else {
      setStatus("streaming");
      setError(null);
    }

    setStage(stageLabel);
    setMessages((prev) => {
      const updated = [...prev];
      const last = updated[updated.length - 1];
      if (last?.role !== "assistant") {
        return updated;
      }

      const hasResults = results.length > 0;
      const engagement = getEngagementQuestion(live.query, hasResults);
      const content = isError
        ? `Something went wrong: ${errorEvent?.message || "Unknown error"}`
        : hasResults
          ? "Here are the moments I found:"
          : isComplete
            ? "I couldn't find any matching moments. Try rephrasing your query?"
            : "";

      updated[updated.length - 1] = {
        ...last,
        content,
        videoResults: results,
        isLoading: !isComplete && !isError,
        stage: isComplete || isError ? undefined : stageLabel,
        traceSteps,
        engagementQuestion: isComplete ? engagement : undefined,
      };
      return updated;
    });
  }, [computeTraceSteps, mapResults, setMessages, stage]);

  const startLiveSubscriptions = useCallback(
    (searchId: string, query: string) => {
      if (!convexClient) {
        throw new Error(
          "NEXT_PUBLIC_CONVEX_URL is not set; Convex live updates are unavailable."
        );
      }

      clearSubscriptions();
      liveStateRef.current.query = query;

      const resultsWatch = convexClient.watchQuery(resultsBySearchRef, {
        searchId,
      });
      const eventsWatch = convexClient.watchQuery(eventsBySearchRef, {
        searchId,
        limit: 120,
      });
      const searchWatch = convexClient.watchQuery(searchGetRef, {
        id: searchId,
      });

      const unsubscribeResults = resultsWatch.onUpdate(() => {
        liveStateRef.current.results =
          (resultsWatch.localQueryResult() as SearchResultDoc[] | undefined) || [];
        syncAssistantFromLiveData();
      });
      const unsubscribeEvents = eventsWatch.onUpdate(() => {
        liveStateRef.current.events =
          (eventsWatch.localQueryResult() as SearchEventDoc[] | undefined) || [];
        syncAssistantFromLiveData();
      });
      const unsubscribeSearch = searchWatch.onUpdate(() => {
        liveStateRef.current.search =
          (searchWatch.localQueryResult() as SearchDoc | undefined) || null;
        syncAssistantFromLiveData();
      });

      // Prime UI immediately with the latest known snapshots so we don't
      // depend on a subsequent update tick to render trace/results.
      liveStateRef.current.results =
        (resultsWatch.localQueryResult() as SearchResultDoc[] | undefined) || [];
      liveStateRef.current.events =
        (eventsWatch.localQueryResult() as SearchEventDoc[] | undefined) || [];
      liveStateRef.current.search =
        (searchWatch.localQueryResult() as SearchDoc | undefined) || null;
      syncAssistantFromLiveData();

      unsubscribersRef.current = [
        unsubscribeResults,
        unsubscribeEvents,
        unsubscribeSearch,
      ];
    },
    [clearSubscriptions, syncAssistantFromLiveData]
  );

  const search = useCallback(
    (query: string) => {
      // Abort any in-flight request
      abortSearch();

      setStatus("loading");
      setStage("");
      setError(null);

      // Add user message
      const userMsg: Message = { role: "user", content: query };

      // Add placeholder assistant message (loading state)
      const assistantMsg: Message = {
        role: "assistant",
        content: "",
        videoResults: [],
        isLoading: true,
        stage: "Starting search...",
        traceSteps: [],
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      const controller = new AbortController();
      requestAbortRef.current = controller;

      liveStateRef.current.query = query;
      liveStateRef.current.search = null;
      liveStateRef.current.events = [];
      liveStateRef.current.results = [];

      void (async () => {
        try {
          const response = await startFindrSearch(
            query,
            conversationRef.current,
            controller.signal
          );
          if (controller.signal.aborted) return;
          setStatus("streaming");
          setStage("Classifying your query...");
          startLiveSubscriptions(response.search_id, query);
        } catch (err) {
          if ((err as Error).name === "AbortError") return;

          const message = (err as Error).message || "Unknown error";
          setStatus("error");
          setError(message);
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: `Something went wrong: ${message}`,
                isLoading: false,
                stage: undefined,
              };
            }
            return updated;
          });
        }
      })();

      conversationRef.current += `\nUser: ${query}`;
    },
    [abortSearch, setMessages, startLiveSubscriptions]
  );

  return {
    search,
    status,
    stage,
    error,
    abortSearch,
  };
}
