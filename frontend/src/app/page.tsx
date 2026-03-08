"use client";

import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowUp,
  Download,
} from "lucide-react";
import ShinyText from "@/components/ShinyText";
import {
  useViralFix,
  type JobEventInfo,
} from "@/hooks/useViralFix";

// ─────────────────────────────────────────────────────────────
// Types + helpers
// ─────────────────────────────────────────────────────────────

type Payload = Record<string, unknown>;
const waveformPattern = [20, 36, 26, 42, 18, 34, 24, 38, 16, 30, 22, 40];

function parse(event: JobEventInfo): Payload {
  if (!event.dataJson) return {};
  try { return JSON.parse(event.dataJson); } catch { return {}; }
}

function toMediaUrl(localPath?: string): string | undefined {
  if (!localPath) return undefined;
  const p = localPath.replace(/\\/g, "/");
  const di = p.indexOf("downloads/");
  if (di >= 0) return `/api/editr/media/downloads/${p.slice(di + 10)}`;
  const oi = p.indexOf("outputs/");
  if (oi >= 0) return `/api/editr/media/outputs/${p.slice(oi + 8)}`;
  return undefined;
}

function shortClipId(videoId?: string): string {
  if (!videoId) return "unknown";
  return videoId.slice(-6);
}

const shiny = new Set(["scanning","tiktok","analyzing","downloading","editing","generating","gemini","ffmpeg","uploading","rendering","browser","agent","filtering","checking"]);

function traceText(text: string) {
  return text.split(/(\s+)/).map((part, i) => {
    const t = part.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
    if (!t || !shiny.has(t)) return <span key={i}>{part}</span>;
    return <ShinyText key={i} text={part} speed={2} delay={0} color="#9ca3af" shineColor="#ffffff" spread={120} direction="left" yoyo={false} pauseOnHover={false} disabled={false} />;
  });
}

function latestTrace(events: JobEventInfo[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.message) return e.message;
    const d = parse(e);
    if (d.message) return d.message as string;
    if (d.stage) return d.stage as string;
  }
  return "Starting...";
}

// ─────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────

export default function Home() {
  const [mode, setMode] = useState<"landing" | "chat">("landing");
  const [input, setInput] = useState("");
  const editr = useViralFix();

  const trace = useMemo(() => latestTrace(editr.events), [editr.events]);
  const isWorking = editr.status !== "idle" && editr.status !== "complete" && editr.status !== "error";
  const latestVideoProgress = useMemo(() => {
    const out = new Map<string, { step: string; status: string; message: string }>();
    for (const e of editr.events) {
      if (e.eventType !== "video_progress") continue;
      const d = parse(e);
      const videoId = d.videoId as string | undefined;
      if (!videoId) continue;
      out.set(videoId, {
        step: (d.step as string) || "editing",
        status: (d.status as string) || "",
        message: (d.message as string) || "",
      });
    }
    return out;
  }, [editr.events]);

  // Collect downloaded videos from video_scored events
  const downloaded = useMemo(() => {
    const out: { id: string; title: string; duration: number; path?: string }[] = [];
    for (const e of editr.events) {
      if (e.eventType !== "video_scored") continue;
      const d = parse(e);
      if (!d.videoId) continue;
      out.push({
        id: d.videoId as string,
        title: (d.title as string) || "",
        duration: Number(d.duration || 0),
        path: d.localPath as string,
      });
    }
    return out;
  }, [editr.events]);

  const musicPreviews = useMemo(() => {
    const out: {
      videoId: string;
      path: string;
      prompt: string;
      bpm?: number;
      energy?: string;
    }[] = [];
    const seen = new Set<string>();

    for (const e of editr.events) {
      const d = parse(e);
      const videoId = (d.videoId as string) || "";
      const musicPayloads = [d.music, d.musicPreview];

      for (const payload of musicPayloads) {
        if (!payload || typeof payload !== "object") continue;
        if (!("localPath" in payload)) continue;

        const music = payload as {
          localPath: string;
          prompt?: string;
          bpm?: number;
          energy?: string;
        };
        if (!music.localPath || seen.has(music.localPath)) continue;
        seen.add(music.localPath);
        out.push({
          videoId,
          path: music.localPath,
          prompt: music.prompt || "",
          bpm: music.bpm,
          energy: music.energy,
        });
      }
    }

    return out;
  }, [editr.events]);

  // Collect all overlay images that have been generated (from video_progress events with overlay data)
  // Collect overlay images from both video_progress and video_complete events
  const overlayImages = useMemo(() => {
    const out: { word: string; path: string; timestamp: number; videoId: string }[] = [];
    const seen = new Set<string>();

    for (const e of editr.events) {
      const d = parse(e);
      const videoId = (d.videoId as string) || "";

      // Check overlays field (from video_progress events)
      const overlays = d.overlays as unknown[];
      if (Array.isArray(overlays)) {
        for (const ov of overlays) {
          if (typeof ov === "object" && ov && "localPath" in ov && "spokenText" in ov) {
            const o = ov as { localPath: string; spokenText: string; timestamp: number };
            const key = `${o.spokenText}-${o.localPath}`;
            if (o.localPath && !seen.has(key)) {
              seen.add(key);
              out.push({ word: o.spokenText, path: o.localPath, timestamp: o.timestamp, videoId });
            }
          }
        }
      }

      // Check overlayPreviews field (from video_complete events)
      const previews = d.overlayPreviews as unknown[];
      if (Array.isArray(previews)) {
        for (const ov of previews) {
          if (typeof ov === "object" && ov && "localPath" in ov && "spokenText" in ov) {
            const o = ov as { localPath: string; spokenText: string; timestamp: number };
            const key = `${o.spokenText}-${o.localPath}`;
            if (o.localPath && !seen.has(key)) {
              seen.add(key);
              out.push({ word: o.spokenText, path: o.localPath, timestamp: o.timestamp, videoId });
            }
          }
        }
      }
    }
    return out;
  }, [editr.events]);

  // Collect completed edits
  const edited = useMemo(() => {
    const out: {
      id: string;
      title: string;
      summary: string;
      url?: string;
      localPath?: string;
      overlays: number;
      hasMusic: boolean;
      captionCount: number;
    }[] = [];
    for (const e of editr.events) {
      if (e.eventType !== "video_complete") continue;
      const d = parse(e);
      out.push({
        id: (d.videoId as string) || "",
        title: (d.title as string) || (d.summary as string) || "",
        summary: (d.summary as string) || "",
        url: d.editedUrl as string,
        localPath: d.localPath as string,
        overlays: Number(d.overlays || 0),
        hasMusic: Boolean(d.hasMusic),
        captionCount: Number(d.captionCount || 0),
      });
    }
    return out;
  }, [editr.events]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim()) return;
    setMode("chat");
    editr.startEdit(input.trim(), "tiktok", 3);
  };

  return (
    <div className="mesh-bg relative h-screen overflow-hidden">
      <div className="relative z-10 flex h-full w-full min-w-0 flex-col overflow-hidden">
        {mode === "landing" && <div className="flex-1" />}

        <AnimatePresence>
          {mode === "landing" && (
            <motion.div key="landing" className="flex flex-col items-center px-4" exit={{ opacity: 0, y: -40, filter: "blur(10px)" }} transition={{ duration: 0.45 }}>
              <motion.p initial={{ opacity: 0, y: -18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.85, delay: 0.1 }} className="mb-4 text-center text-[1.9rem] font-normal tracking-wide text-white/62 sm:text-[2.8rem]">
                Replace your editor with
              </motion.p>
              <motion.h1 initial={{ opacity: 0, y: -30, filter: "blur(10px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 1 }} className="text-white text-[10rem] sm:text-[12rem] font-bold tracking-tighter mb-4 select-none leading-none">
                editr
              </motion.h1>
              <motion.p initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, delay: 0.4 }} className="text-white/40 text-xl sm:text-[1.7rem] mb-12 font-normal tracking-wide">
                you made the video. we make it go viral.
              </motion.p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Chat */}
        {mode === "chat" && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.3 }} className="flex-1 overflow-y-auto px-4 py-8">
            <div className="max-w-3xl mx-auto space-y-6">

              {/* Live trace — always visible while working, bigger text */}
              {isWorking && (
                <AnimatePresence mode="wait">
                  <motion.p
                    key={trace}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.25 }}
                    className="text-[21px] text-[#9ca3af] font-normal leading-relaxed"
                  >
                    {traceText(trace)}
                  </motion.p>
                </AnimatePresence>
              )}

              {/* Music previews — one generated soundtrack per clip */}
              {musicPreviews.length > 0 && (
                <div>
                  <p className="text-[11px] text-white/20 uppercase tracking-[0.2em] mb-3">Soundtracks</p>
                  <div className="flex gap-3 overflow-x-auto pb-2">
                    {musicPreviews.map((track) => {
                      const url = toMediaUrl(track.path);
                      return (
                        <motion.div
                          key={track.path}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          className="shrink-0 w-[220px] rounded-2xl border border-cyan-400/10 bg-black/40 p-4"
                        >
                          <div className="flex items-center justify-between mb-3">
                            <p className="text-[11px] uppercase tracking-[0.18em] text-cyan-200/65">
                              Clip {shortClipId(track.videoId)}
                            </p>
                            <p className="text-[10px] text-white/35">
                              {track.bpm ? `${track.bpm} BPM` : "Generated"}
                            </p>
                          </div>

                          <div className="mb-3 flex h-11 items-end gap-1">
                            {waveformPattern.map((height, index) => (
                              <div
                                key={`${track.path}-${index}`}
                                className="w-2 rounded-full bg-gradient-to-t from-cyan-500/40 via-sky-300/70 to-white/80"
                                style={{ height }}
                              />
                            ))}
                          </div>

                          <p className="mb-1 text-[11px] text-white/55 line-clamp-2">
                            {track.prompt || "Generated background soundtrack"}
                          </p>
                          <p className="mb-3 text-[10px] uppercase tracking-[0.18em] text-white/28">
                            {track.energy || "Ambient"}
                          </p>

                          {url ? (
                            <audio
                              controls
                              preload="none"
                              src={url}
                              className="w-full h-10 opacity-85"
                            />
                          ) : (
                            <div className="h-10 rounded-xl border border-white/[0.05] bg-white/[0.03]" />
                          )}
                        </motion.div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Downloaded videos — small 9:16 cards side by side */}
              {downloaded.length > 0 && (
                <div>
                  <p className="text-[11px] text-white/20 uppercase tracking-[0.2em] mb-3">Downloaded</p>
                  <div className="flex gap-3 overflow-x-auto pb-2">
                    {downloaded.map((v) => {
                      const url = toMediaUrl(v.path);
                      return (
                        <motion.div
                          key={v.id}
                          initial={{ opacity: 0, scale: 0.95 }}
                          animate={{ opacity: 1, scale: 1 }}
                          className="shrink-0 w-[120px] rounded-xl overflow-hidden border border-white/[0.06] bg-black/40"
                        >
                          {url ? (
                            <video src={url} className="clip-player aspect-[9/16] w-full object-cover" preload="metadata" playsInline controls />
                          ) : (
                            <div className="aspect-[9/16] w-full bg-white/[0.02]" />
                          )}
                          <div className="px-2 py-1.5">
                            <p className="text-[11px] text-white/50 truncate">{v.title || `${v.duration}s`}</p>
                            <p className="mt-1 text-[10px] text-cyan-100/40 line-clamp-2">
                              {latestVideoProgress.get(v.id)?.message || "Queued for captions, music, and overlays"}
                            </p>
                          </div>
                        </motion.div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Overlay images — grouped by video, pop in as they generate */}
              {overlayImages.length > 0 && (() => {
                // Group overlays by videoId
                const grouped = new Map<string, typeof overlayImages>();
                for (const ov of overlayImages) {
                  const key = ov.videoId || "unknown";
                  if (!grouped.has(key)) grouped.set(key, []);
                  grouped.get(key)!.push(ov);
                }
                return (
                  <div className="space-y-4">
                    {Array.from(grouped.entries()).map(([videoId, ovs]) => (
                      <div key={videoId}>
                        <p className="text-[11px] text-white/20 uppercase tracking-[0.2em] mb-2">
                          Overlays for clip {videoId.slice(-6)}
                        </p>
                        <div className="flex gap-2.5 flex-wrap">
                          {ovs.map((ov, i) => {
                            const url = toMediaUrl(ov.path);
                            return (
                              <motion.div
                                key={`${ov.word}-${i}`}
                                initial={{ opacity: 0, scale: 0.8 }}
                                animate={{ opacity: 1, scale: 1 }}
                                transition={{ delay: i * 0.08 }}
                                className="w-[90px] rounded-xl overflow-hidden border border-white/[0.06] bg-black/30"
                              >
                                {url ? (
                                  <img src={url} alt={ov.word} className="aspect-square w-full object-cover" />
                                ) : (
                                  <div className="aspect-square w-full bg-white/[0.03] flex items-center justify-center text-white/15 text-[10px]">
                                    generating...
                                  </div>
                                )}
                                <p className="px-1.5 py-1.5 text-[10px] text-white/45 truncate text-center">{ov.word}</p>
                              </motion.div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}

              {/* Edited videos — small 9:16 cards side by side with download */}
              {edited.length > 0 && (
                <div>
                  <p className="text-[11px] text-white/20 uppercase tracking-[0.2em] mb-3">Edited</p>
                  <div className="flex gap-3 overflow-x-auto pb-2">
                    {edited.map((v) => {
                      // Prefer local path over GCS URL — GCS bucket has uniform access
                      // which blocks public reads, so the GCS URL won't work
                      const url = toMediaUrl(v.localPath) || v.url;
                      return (
                        <motion.div
                          key={v.id}
                          initial={{ opacity: 0, scale: 0.95 }}
                          animate={{ opacity: 1, scale: 1 }}
                          className="shrink-0 w-[140px] rounded-xl overflow-hidden border border-emerald-500/15 bg-black/40"
                        >
                          {url ? (
                            <video src={url} className="clip-player aspect-[9/16] w-full object-cover bg-black" controls preload="metadata" playsInline />
                          ) : (
                            <div className="aspect-[9/16] w-full bg-white/[0.02]" />
                          )}
                          <div className="px-2 py-2">
                            <p className="text-[11px] text-white/50 truncate mb-1.5">{v.summary || v.title || "Edited"}</p>
                            <div className="mb-2 flex flex-wrap gap-1.5">
                              <span className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] text-white/40">
                                {v.overlays} overlays
                              </span>
                              {v.captionCount > 0 && (
                                <span className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] text-white/40">
                                  captions on
                                </span>
                              )}
                              {v.hasMusic && (
                                <span className="rounded-full border border-emerald-400/15 px-2 py-0.5 text-[10px] text-emerald-200/55">
                                  background music
                                </span>
                              )}
                            </div>
                            {url && (
                              <a
                                href={url}
                                download
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center justify-center gap-1 w-full py-1.5 rounded-md bg-white/10 hover:bg-white/15 text-[11px] text-white/70 font-medium transition-colors"
                              >
                                <Download className="w-3 h-3" />
                                Download
                              </a>
                            )}
                          </div>
                        </motion.div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Error */}
              {editr.error && <p className="text-red-400/70 text-[14px]">{editr.error}</p>}

              {/* Done with nothing */}
              {editr.status === "complete" && edited.length === 0 && downloaded.length === 0 && (
                <p className="text-white/25 text-[14px]">No editable videos found. Try a different account.</p>
              )}
            </div>
          </motion.div>
        )}

        {/* Input */}
        <motion.div layout transition={{ type: "spring", stiffness: 200, damping: 30 }} className={`w-full max-w-3xl mx-auto px-4 ${mode === "chat" ? "pb-4" : ""}`}>
          <motion.div initial={{ opacity: 0, y: 30, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.8, delay: 0.6 }}>
            <form onSubmit={handleSubmit} className="relative group z-20">
              <div className={`relative rounded-2xl p-[1px] bg-gradient-to-r from-[#A66CF9] via-[#3B82F6] to-[#2DD4BF] transition-opacity duration-500 ${input ? "opacity-100" : "opacity-50 group-focus-within:opacity-100"}`}>
                <div className={`relative bg-[#050505] rounded-2xl overflow-hidden flex flex-col ${mode === "chat" ? "min-h-[70px]" : "min-h-[140px]"}`}>
                  <div className="relative flex-1">
                    <input
                      type="text"
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSubmit(); } }}
                      className={`w-full bg-transparent border-none px-6 py-5 text-xl font-medium text-white focus:outline-none font-sans ${mode === "chat" ? "min-h-[30px]" : "min-h-[50px] mt-4"}`}
                    />
                  </div>
                  <div className="flex items-center justify-end px-5 pb-4">
                    <button type="submit" disabled={!input.trim()} className="flex items-center justify-center w-9 h-9 rounded-full bg-white text-[#1a1a1a] disabled:opacity-20 disabled:cursor-not-allowed hover:bg-zinc-200 active:scale-95 transition-all duration-150">
                      <ArrowUp className="w-5 h-5" strokeWidth={2.5} />
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </motion.div>
        </motion.div>

        {mode === "landing" && <div className="flex-1" />}
      </div>
    </div>
  );
}
