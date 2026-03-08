import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const add = mutation({
  args: {
    jobId: v.id("jobs"),
    eventType: v.string(),
    message: v.optional(v.string()),
    dataJson: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("jobEvents", {
      ...args,
      createdAt: Date.now(),
    });
  },
});

export const byJob = query({
  args: {
    jobId: v.id("jobs"),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const items = await ctx.db
      .query("jobEvents")
      .withIndex("by_job_created", (q) => q.eq("jobId", args.jobId))
      .collect();

    const limit = args.limit ?? items.length;
    if (limit >= items.length) {
      return items;
    }
    return items.slice(items.length - limit);
  },
});
