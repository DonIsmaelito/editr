"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowUp,
  Plus,
  Clock,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import TextType from "@/components/TextType";
import ShinyText from "@/components/ShinyText";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import {
  useFindrSearch,
  type Message,
} from "@/hooks/useFindrSearch";

// ─────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────

const recentItems = [
  "monkey videos",
  "ml math",
  "beach vibes",
  "cooking tips",
  "cs 224w",
];

const shinyTraceWords = new Set([
  "interpreting",
  "searching",
  "chunking",
  "ranking",
  "pinpointing",
  "youtube",
  "tiktok",
  "twitter",
  "x",
]);

function renderLiveTraceText(text: string) {
  const parts = text.split(/(\s+)/);

  return parts.map((part, index) => {
    const token = part.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
    if (!token || !shinyTraceWords.has(token)) {
      return <span key={`${part}-${index}`}>{part}</span>;
    }

    return (
      <ShinyText
        key={`${part}-${index}`}
        text={part}
        speed={2}
        delay={0}
        color="#9ca3af"
        shineColor="#ffffff"
        spread={120}
        direction="left"
        yoyo={false}
        pauseOnHover={false}
        disabled={false}
      />
    );
  });
}

// ─────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────

export default function Home() {
  const [query, setQuery] = useState("");
  const [showTypewriter, setShowTypewriter] = useState(true);
  const [mode, setMode] = useState<"landing" | "chat">("landing");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const latestQueryRef = useRef<HTMLDivElement>(null);
  const shouldFocusLatestQueryRef = useRef(false);
  const focusRafRef = useRef<number | null>(null);
  const focusTimeoutRef = useRef<number | null>(null);

  const {
    search,
    abortSearch,
  } = useFindrSearch(setMessages);

  // On each submit, snap the newest user message to the top of the chat scroller.
  useEffect(() => {
    if (!shouldFocusLatestQueryRef.current) {
      return;
    }

    let attempts = 0;

    const clearPending = () => {
      if (focusRafRef.current !== null) {
        cancelAnimationFrame(focusRafRef.current);
        focusRafRef.current = null;
      }
      if (focusTimeoutRef.current !== null) {
        window.clearTimeout(focusTimeoutRef.current);
        focusTimeoutRef.current = null;
      }
    };

    const snapLatestToTop = () => {
      const container = chatScrollRef.current;
      const target = latestQueryRef.current;

      // Chat mode / target node can appear one frame later after mode switch.
      if (!container || !target) {
        attempts += 1;
        if (attempts < 20) {
          focusRafRef.current = requestAnimationFrame(snapLatestToTop);
        }
        return;
      }

      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const targetTop = container.scrollTop + (targetRect.top - containerRect.top) - 12;

      container.scrollTo({
        top: Math.max(0, targetTop),
        behavior: "smooth",
      });

      // Framer motion shifts elements during enter animation; correct once more.
      focusTimeoutRef.current = window.setTimeout(() => {
        const liveContainer = chatScrollRef.current;
        const liveTarget = latestQueryRef.current;
        if (!liveContainer || !liveTarget) return;

        const liveContainerRect = liveContainer.getBoundingClientRect();
        const liveTargetRect = liveTarget.getBoundingClientRect();
        const correctedTop =
          liveContainer.scrollTop + (liveTargetRect.top - liveContainerRect.top) - 12;

        liveContainer.scrollTo({
          top: Math.max(0, correctedTop),
          behavior: "smooth",
        });
      }, 180);

      shouldFocusLatestQueryRef.current = false;
    };

    snapLatestToTop();
    return clearPending;
  }, [messages, mode]);

  // Abort on unmount
  useEffect(() => {
    return () => abortSearch();
  }, [abortSearch]);

  const handleFocus = () => {
    setShowTypewriter(false);
  };

  const handleBlur = () => {
    if (!query.trim() && mode === "landing") {
      setShowTypewriter(true);
    }
  };

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!query.trim()) return;

    const q = query.trim();
    setQuery("");
    setShowTypewriter(false);

    if (mode === "landing") {
      setMode("chat");
    }

    shouldFocusLatestQueryRef.current = true;
    search(q);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  let latestUserMessageIndex = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "user") {
      latestUserMessageIndex = i;
      break;
    }
  }

  return (
    <div className="mesh-bg flex h-screen relative overflow-hidden">
      {/* ── Sidebar ── */}
      <motion.aside
        animate={{ width: sidebarOpen ? 260 : 68 }}
        transition={{ duration: 0.3, ease: "easeInOut" }}
        className="relative z-20 flex flex-col border-r border-white/5 bg-[#09090b]/80 backdrop-blur-xl overflow-hidden shrink-0"
      >
        {/* findr logo */}
        <div className="pt-6 pb-4 min-h-[80px] flex items-center justify-center">
          <AnimatePresence>
            {sidebarOpen && (
              <motion.span
                key="sidebar-logo"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="text-white text-7xl font-bold tracking-tighter select-none"
              >
                findr
              </motion.span>
            )}
          </AnimatePresence>
        </div>

        {/* Nav items */}
        <nav className="flex flex-col gap-1 px-3">
          <button className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-white/70 hover:text-white hover:bg-white/5 transition-colors duration-150 group">
            <Plus className="w-5 h-5 shrink-0" strokeWidth={2} />
            {sidebarOpen && (
              <span className="text-[15px] font-medium whitespace-nowrap overflow-hidden">
                New Thread
              </span>
            )}
          </button>

          <button className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-white/70 hover:text-white hover:bg-white/5 transition-colors duration-150">
            <Clock className="w-5 h-5 shrink-0" strokeWidth={2} />
            {sidebarOpen && (
              <span className="text-[15px] font-medium whitespace-nowrap overflow-hidden">
                History
              </span>
            )}
          </button>
        </nav>

        {/* Recent section */}
        <AnimatePresence>
          {sidebarOpen && (
            <motion.div
              key="recent-section"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="flex flex-col mt-4 overflow-hidden"
            >
              <div className="mx-4 border-t border-white/5" />
              <span className="px-6 pt-4 pb-2 text-xs font-medium text-white/30 uppercase tracking-wider">
                Recent
              </span>
              <div className="flex flex-col gap-0.5 px-3">
                {recentItems.map((item) => (
                  <button
                    key={item}
                    className="px-3 py-2 rounded-xl text-white/50 hover:text-white/80 hover:bg-white/5 transition-colors duration-150 text-left text-[14px] truncate"
                  >
                    {item}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Collapse toggle */}
        <div className="px-3 pb-4">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="flex items-center justify-center w-full py-2.5 rounded-xl text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors duration-150"
          >
            {sidebarOpen ? (
              <PanelLeftClose className="w-5 h-5" strokeWidth={1.5} />
            ) : (
              <PanelLeftOpen className="w-5 h-5" strokeWidth={1.5} />
            )}
          </button>
        </div>
      </motion.aside>

      {/* ── Main content ── */}
      <div className="relative z-10 flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* Landing mode: top spacer for vertical centering */}
        {mode === "landing" && <div className="flex-1" />}

        {/* Landing mode: title + slogan */}
        <AnimatePresence>
          {mode === "landing" && (
            <motion.div
              key="landing-header"
              className="flex flex-col items-center px-4"
              exit={{ opacity: 0, y: -40, filter: "blur(10px)" }}
              transition={{ duration: 0.45, ease: "easeInOut" }}
            >
              <motion.h1
                initial={{ opacity: 0, y: -30, filter: "blur(10px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                transition={{ duration: 1, ease: "easeOut" }}
                className="text-white text-[10rem] sm:text-[12rem] font-bold tracking-tighter mb-4 select-none leading-none"
              >
                findr
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.8, delay: 0.4 }}
                className="text-white/40 text-xl sm:text-[1.7rem] mb-12 font-normal tracking-wide"
              >
                every video has a moment — we find it.
              </motion.p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Chat mode: scrollable messages area ── */}
        {mode === "chat" && (
          <motion.div
            ref={chatScrollRef}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4, delay: 0.4 }}
            className="flex-1 overflow-y-auto px-4 py-6"
          >
            <div className="max-w-4xl mx-auto space-y-10">
              {messages.map((msg, i) => {
                const isLastUserMsg =
                  msg.role === "user" &&
                  i === latestUserMessageIndex;

                return (
                  <motion.div
                    key={i}
                    ref={isLastUserMsg ? latestQueryRef : undefined}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3 }}
                    className={`flex ${
                      msg.role === "user" ? "justify-end" : "justify-start"
                    }`}
                  >
                    {/* ── User bubble ── */}
                    {msg.role === "user" && (
                      <div className="max-w-[75%] bg-[#1a1a1c] rounded-2xl px-5 py-3">
                        <p className="text-white font-semibold text-lg">
                          {msg.content}
                        </p>
                      </div>
                    )}

                    {/* ── Assistant response ── */}
                    {msg.role === "assistant" && (
                      <div className="max-w-[85%] w-full">
                        {/* Single-line live trace (latest Convex message only) */}
                        {msg.isLoading && (
                          <p className="mb-4 text-[17px] font-normal text-[#9ca3af] leading-relaxed">
                            {renderLiveTraceText(
                              msg.traceSteps && msg.traceSteps.length > 0
                                ? msg.traceSteps[msg.traceSteps.length - 1].label
                                : (msg.stage || "Thinking...")
                            )}
                          </p>
                        )}

                        {/* Response text — shown when we have content */}
                        {msg.content && !msg.isLoading && (
                          <p className="text-white font-semibold text-lg mb-5">
                            {msg.content}
                          </p>
                        )}

                        {/* ── Collapsibles (video results) ── */}
                        {msg.videoResults && msg.videoResults.length > 0 && (
                          <div className="space-y-3 mb-5">
                            {msg.videoResults.map((video, vi) => (
                              <motion.div
                                key={`${video.videoId || video.videoName}-${vi}`}
                                initial={{ opacity: 0, y: 8 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{
                                  duration: 0.3,
                                  delay: vi * 0.1,
                                }}
                              >
                                <Collapsible className="rounded-xl border border-white/5 bg-white/[0.02]">
                                  <CollapsibleTrigger className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-white/[0.03] rounded-xl transition-colors duration-150">
                                    <ChevronRight
                                      className="w-5 h-5 shrink-0 text-white/40 transition-transform duration-200 [[data-state=open]>&]:rotate-90"
                                      strokeWidth={2}
                                    />
                                    <span className="text-white font-semibold text-lg truncate">
                                      {video.videoName}
                                    </span>
                                  </CollapsibleTrigger>

                                  <CollapsibleContent className="px-4 pb-4 pt-1">
                                    {/* Video embed */}
                                    {video.embedUrl && (
                                      <div className="rounded-lg overflow-hidden aspect-video">
                                        <iframe
                                          src={video.embedUrl}
                                          className="w-full h-full"
                                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                                          allowFullScreen
                                          title={video.videoName}
                                        />
                                      </div>
                                    )}
                                  </CollapsibleContent>
                                </Collapsible>
                              </motion.div>
                            ))}
                          </div>
                        )}

                        {/* ── Separator + engagement (only after successful results) ── */}
                        {!msg.isLoading &&
                          msg.videoResults &&
                          msg.videoResults.length > 0 && (
                          <>
                            <Separator className="bg-white/10 my-5" />
                            <p className="text-white font-semibold text-base">
                              {msg.engagementQuestion ||
                                "What other videos would you like to see next?"}
                            </p>
                          </>
                        )}
                      </div>
                    )}
                  </motion.div>
                );
              })}

            </div>
          </motion.div>
        )}

        {/* ── Chatbox ── */}
        <motion.div
          layout
          transition={{ type: "spring", stiffness: 200, damping: 30 }}
          className={`w-full max-w-4xl mx-auto px-4 ${
            mode === "chat" ? "pb-4" : ""
          }`}
        >
          <motion.div
            initial={{ opacity: 0, y: 30, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ duration: 0.8, delay: 0.6 }}
          >
            <form onSubmit={handleSubmit} className="relative group z-20">
              <div
                className={`relative rounded-2xl p-[1px] bg-gradient-to-r from-[#A66CF9] via-[#3B82F6] to-[#2DD4BF] transition-opacity duration-500 ${
                  !showTypewriter || query
                    ? "opacity-100"
                    : "opacity-50 group-focus-within:opacity-100"
                }`}
              >
                <div
                  className={`relative bg-[#050505] rounded-2xl overflow-hidden flex flex-col ${
                    mode === "chat" ? "min-h-[120px]" : "min-h-[200px]"
                  }`}
                >
                  <div className="relative flex-1">
                    {showTypewriter && !query && mode === "landing" && (
                      <div className="absolute inset-0 flex items-start pointer-events-none px-6 py-5">
                        <TextType
                          text={[
                            "Find me monkey videos on YouTube.",
                            "Help me learn about machine learning math.",
                            "I want to see videos of touristy beaches in Mexico.",
                          ]}
                          typingSpeed={110}
                          pauseDuration={1500}
                          deletingSpeed={50}
                          showCursor
                          cursorCharacter="|"
                          cursorBlinkDuration={0.5}
                          className="text-[#9ca3af] text-xl font-medium font-sans"
                          cursorClassName="text-[#9ca3af]"
                        />
                      </div>
                    )}

                    <textarea
                      ref={textareaRef}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onFocus={handleFocus}
                      onBlur={handleBlur}
                      onKeyDown={handleKeyDown}
                      placeholder={
                        mode === "chat"
                          ? "Ask a follow-up..."
                          : !showTypewriter && !query
                            ? "Describe what you want to watch..."
                            : ""
                      }
                      className={`w-full bg-transparent border-none px-6 py-5 text-xl font-medium text-white placeholder:text-[#52525B] focus:outline-none resize-none flex-1 custom-scrollbar font-sans ${
                        mode === "chat" ? "min-h-[70px]" : "min-h-[140px]"
                      }`}
                    />
                  </div>

                  <div className="flex items-center justify-end px-5 pb-5">
                    <button
                      type="submit"
                      disabled={!query.trim()}
                      className="flex items-center justify-center w-9 h-9 rounded-full bg-white text-[#1a1a1a] disabled:opacity-20 disabled:cursor-not-allowed hover:bg-zinc-200 active:scale-95 transition-all duration-150"
                    >
                      <ArrowUp className="w-5 h-5" strokeWidth={2.5} />
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </motion.div>
        </motion.div>

        {/* Landing mode: bottom spacer */}
        {mode === "landing" && <div className="flex-1" />}
      </div>
    </div>
  );
}
