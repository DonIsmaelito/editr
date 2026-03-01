import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const addResult = mutation({
  args: {
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
    order: v.number(),
    subQueryTitle: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const id = await ctx.db.insert("searchResults", args);

    // Increment result count on parent search
    const search = await ctx.db.get(args.searchId);
    if (search) {
      await ctx.db.patch(args.searchId, {
        resultCount: search.resultCount + 1,
      });
    }

    return id;
  },
});

// Frontend subscribes to this — auto-rerenders as results arrive
export const bySearch = query({
  args: { searchId: v.id("searches") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("searchResults")
      .withIndex("by_search", (q) => q.eq("searchId", args.searchId))
      .collect();
  },
});
