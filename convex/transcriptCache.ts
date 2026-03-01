import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const getByVideoId = query({
  args: { videoId: v.string() },
  handler: async (ctx, args) => {
    const result = await ctx.db
      .query("transcriptCache")
      .withIndex("by_video", (q) => q.eq("videoId", args.videoId))
      .first();
    return result;
  },
});

export const insert = mutation({
  args: {
    videoId: v.string(),
    platform: v.string(),
    segments: v.string(), // JSON stringified
  },
  handler: async (ctx, args) => {
    // Upsert: delete existing cache for this video first
    const existing = await ctx.db
      .query("transcriptCache")
      .withIndex("by_video", (q) => q.eq("videoId", args.videoId))
      .first();
    if (existing) {
      await ctx.db.delete(existing._id);
    }

    return await ctx.db.insert("transcriptCache", {
      videoId: args.videoId,
      platform: args.platform,
      segments: args.segments,
      fetchedAt: Date.now(),
    });
  },
});
