import { action, internalQuery, mutation } from "./_generated/server";
import { internal } from "./_generated/api";
import { v } from "convex/values";

// -----------------------------------------------------------------
// Insert a transcript segment with its embedding vector
// -----------------------------------------------------------------
export const insert = mutation({
  args: {
    videoId: v.string(),
    segmentIndex: v.number(),
    startTime: v.number(),
    endTime: v.number(),
    text: v.string(),
    embedding: v.array(v.float64()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("transcriptSegments", args);
  },
});

// -----------------------------------------------------------------
// Internal query to fetch segment by ID (used by the action below)
// -----------------------------------------------------------------
export const getById = internalQuery({
  args: { id: v.id("transcriptSegments") },
  handler: async (ctx, args) => {
    return await ctx.db.get(args.id);
  },
});

// -----------------------------------------------------------------
// Vector similarity search (action — required for vectorSearch)
//
// Takes a query embedding + videoId filter, returns the top N
// most similar transcript segments with their similarity scores.
// -----------------------------------------------------------------
export const searchSimilar = action({
  args: {
    queryEmbedding: v.array(v.float64()),
    videoId: v.string(),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const results = await ctx.vectorSearch(
      "transcriptSegments",
      "by_embedding",
      {
        vector: args.queryEmbedding,
        limit: args.limit ?? 2,
        filter: (q) => q.eq("videoId", args.videoId),
      }
    );

    // Fetch full segment data for each result
    const segments = [];
    for (const result of results) {
      const segment = await ctx.runQuery(
        internal.segments.getById,
        { id: result._id }
      );
      if (segment) {
        segments.push({
          ...segment,
          _score: result._score,
        });
      }
    }

    return segments;
  },
});

// -----------------------------------------------------------------
// Delete all segments for a video (cleanup)
// -----------------------------------------------------------------
export const deleteByVideo = mutation({
  args: { videoId: v.string() },
  handler: async (ctx, args) => {
    const segments = await ctx.db
      .query("transcriptSegments")
      .filter((q) => q.eq(q.field("videoId"), args.videoId))
      .collect();

    for (const seg of segments) {
      await ctx.db.delete(seg._id);
    }

    return segments.length;
  },
});
