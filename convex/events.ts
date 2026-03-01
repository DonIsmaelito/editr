import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const add = mutation({
  args: {
    searchId: v.id("searches"),
    eventType: v.union(
      v.literal("status"),
      v.literal("trace"),
      v.literal("moment"),
      v.literal("clarification"),
      v.literal("done"),
      v.literal("error")
    ),
    stage: v.optional(v.string()),
    message: v.optional(v.string()),
    dataJson: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("searchEvents", {
      ...args,
      createdAt: Date.now(),
    });
  },
});

export const bySearch = query({
  args: {
    searchId: v.id("searches"),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const items = await ctx.db
      .query("searchEvents")
      .withIndex("by_search_created", (q) => q.eq("searchId", args.searchId))
      .collect();

    const limit = args.limit ?? items.length;
    if (limit >= items.length) {
      return items;
    }
    return items.slice(items.length - limit);
  },
});
