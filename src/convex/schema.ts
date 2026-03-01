import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // -----------------------------------------------------------------
  // Search Sessions
  // Each user query creates one search session.
  // -----------------------------------------------------------------
  searches: defineTable({
    query: v.string(),
    status: v.union(
      v.literal("classifying"),
      v.literal("searching"),
      v.literal("analyzing"),
      v.literal("complete"),
      v.literal("error")
    ),
    platforms: v.array(v.string()),
    outputFormat: v.union(v.literal("structured"), v.literal("direct")),
    resultCount: v.number(),
    errorMessage: v.optional(v.string()),
    createdAt: v.number(),
  }).index("by_created", ["createdAt"]),

  // -----------------------------------------------------------------
  // Search Results (Moments)
  // Each found moment is one result. Frontend subscribes to these
  // via useQuery and sees results appear progressively.
  // -----------------------------------------------------------------
  searchResults: defineTable({
    searchId: v.id("searches"),
    platform: v.union(
      v.literal("youtube"),
      v.literal("tiktok"),
      v.literal("x")
    ),
    videoId: v.string(),
    embedUrl: v.string(),
    title: v.string(),
    description: v.optional(v.string()),
    startTime: v.number(),
    endTime: v.number(),
    channel: v.optional(v.string()),
    thumbnail: v.optional(v.string()),
    highlightType: v.optional(v.string()),
    relevanceScore: v.optional(v.number()),
    order: v.number(), // Sequence for structured output collapsables
    subQueryTitle: v.optional(v.string()), // Collapsable section header for structured output
  }).index("by_search", ["searchId", "order"]),

  // -----------------------------------------------------------------
  // Transcript Segments (Vector-Indexed)
  // 5-minute chunks of video transcripts with OpenAI embeddings.
  // Used for similarity search to filter before LLM moment finding.
  // -----------------------------------------------------------------
  transcriptSegments: defineTable({
    videoId: v.string(),
    segmentIndex: v.number(),
    startTime: v.number(),
    endTime: v.number(),
    text: v.string(),
    embedding: v.array(v.float64()),
  }).vectorIndex("by_embedding", {
    vectorField: "embedding",
    dimensions: 1536, // OpenAI text-embedding-3-small
    filterFields: ["videoId"],
  }),

  // -----------------------------------------------------------------
  // Transcript Cache
  // Avoids re-fetching transcripts for videos we've already processed.
  // -----------------------------------------------------------------
  transcriptCache: defineTable({
    videoId: v.string(),
    platform: v.string(),
    segments: v.string(), // JSON-stringified transcript segments
    fetchedAt: v.number(),
  }).index("by_video", ["videoId"]),
});
