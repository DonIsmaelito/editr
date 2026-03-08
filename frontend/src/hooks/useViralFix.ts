/**
 * useViralFix — React hook for ViralFix edit jobs.
 *
 * Flow:
 *  1) POST /api/editr/api/edit to start a job, get job_id
 *  2) Subscribe to Convex queries for job status, events, and videos
 *  3) Project live docs into UI state
 */

import { useCallback, useRef, useState } from "react";
import { ConvexReactClient } from "convex/react";
import { makeFunctionReference } from "convex/server";
import { startViralFixEdit } from "@/lib/viralfix-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ProfileInfo {
  username: string;
  followers: number;
  totalLikes: number;
  videoCount: number;
  avgViews: number;
}

export interface VideoInfo {
  _id: string;
  videoId: string;
  title: string;
  duration: number;
  thumbnail?: string;
  views: number;
  likes: number;
  comments: number;
  shares?: number;
  fixabilityScore: number;
  selected: boolean;
  editStatus: string;
  editLevel?: string;
  editedVideoUrl?: string;
  originalUrl: string;
}

export interface JobEventInfo {
  eventType: string;
  message?: string;
  dataJson?: string;
  createdAt: number;
}

export type ViralFixStatus =
  | "idle"
  | "scraping"
  | "scoring"
  | "processing"
  | "complete"
  | "error";

// ---------------------------------------------------------------------------
// Convex function references
// ---------------------------------------------------------------------------

interface JobDoc {
  status: string;
  username: string;
  videosProcessed: number;
  maxVideos: number;
  profileDataJson?: string;
  errorMessage?: string;
}

const jobGetRef = makeFunctionReference<
  "query",
  { id: string },
  JobDoc | null
>("jobs:get");

const jobEventsByJobRef = makeFunctionReference<
  "query",
  { jobId: string; limit?: number },
  JobEventInfo[]
>("jobEvents:byJob");

const videosByJobRef = makeFunctionReference<
  "query",
  { jobId: string },
  VideoInfo[]
>("videos:byJob");

const convexUrl = process.env.NEXT_PUBLIC_CONVEX_URL;
const convexClient = convexUrl ? new ConvexReactClient(convexUrl) : null;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useViralFix() {
  const [status, setStatus] = useState<ViralFixStatus>("idle");
  const [profile, setProfile] = useState<ProfileInfo | null>(null);
  const [videos, setVideos] = useState<VideoInfo[]>([]);
  const [events, setEvents] = useState<JobEventInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const unsubscribersRef = useRef<Array<() => void>>([]);

  const clearSubscriptions = useCallback(() => {
    for (const unsub of unsubscribersRef.current) {
      unsub();
    }
    unsubscribersRef.current = [];
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    clearSubscriptions();
  }, [clearSubscriptions]);

  const startSubscriptions = useCallback(
    (id: string) => {
      if (!convexClient) {
        throw new Error("NEXT_PUBLIC_CONVEX_URL not set");
      }

      clearSubscriptions();

      const jobWatch = convexClient.watchQuery(jobGetRef, { id });
      const eventsWatch = convexClient.watchQuery(jobEventsByJobRef, {
        jobId: id,
        limit: 200,
      });
      const videosWatch = convexClient.watchQuery(videosByJobRef, {
        jobId: id,
      });

      const syncState = () => {
        const job = jobWatch.localQueryResult() as JobDoc | null | undefined;
        const evts = (eventsWatch.localQueryResult() as JobEventInfo[] | undefined) || [];
        const vids = (videosWatch.localQueryResult() as VideoInfo[] | undefined) || [];

        setEvents(evts);
        setVideos(vids);

        if (job) {
          setStatus(job.status as ViralFixStatus);

          if (job.profileDataJson) {
            try {
              setProfile(JSON.parse(job.profileDataJson));
            } catch {}
          }

          if (job.errorMessage) {
            setError(job.errorMessage);
          }
        }
      };

      const unsub1 = jobWatch.onUpdate(syncState);
      const unsub2 = eventsWatch.onUpdate(syncState);
      const unsub3 = videosWatch.onUpdate(syncState);

      unsubscribersRef.current = [unsub1, unsub2, unsub3];

      // Initial sync
      syncState();
    },
    [clearSubscriptions]
  );

  const startEdit = useCallback(
    async (username: string, platform: string = "tiktok", maxVideos: number = 3) => {
      abort();

      setStatus("scraping");
      setProfile(null);
      setVideos([]);
      setEvents([]);
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await startViralFixEdit(
          username,
          platform,
          maxVideos,
          controller.signal
        );

        if (controller.signal.aborted) return;

        setJobId(response.job_id);
        startSubscriptions(response.job_id);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        const message = (err as Error).message || "Unknown error";
        setStatus("error");
        setError(message);
      }
    },
    [abort, startSubscriptions]
  );

  return {
    status,
    profile,
    videos,
    events,
    error,
    jobId,
    startEdit,
    abort,
  };
}
