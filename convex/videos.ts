import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const create = mutation({
  args: {
    jobId: v.id("jobs"),
    platform: v.string(),
    videoId: v.string(),
    originalUrl: v.string(),
    title: v.string(),
    duration: v.number(),
    thumbnail: v.optional(v.string()),
    views: v.number(),
    likes: v.number(),
    comments: v.number(),
    shares: v.optional(v.number()),
    fixabilityScore: v.number(),
    selected: v.boolean(),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("videos", {
      ...args,
      editStatus: "pending",
      createdAt: Date.now(),
    });
  },
});

export const updateEditStatus = mutation({
  args: {
    id: v.id("videos"),
    editStatus: v.string(),
    editLevel: v.optional(v.string()),
    editedVideoUrl: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const patch: Record<string, unknown> = { editStatus: args.editStatus };
    if (args.editLevel !== undefined) {
      patch.editLevel = args.editLevel;
    }
    if (args.editedVideoUrl !== undefined) {
      patch.editedVideoUrl = args.editedVideoUrl;
    }
    await ctx.db.patch(args.id, patch);
  },
});

export const byJob = query({
  args: { jobId: v.id("jobs") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("videos")
      .withIndex("by_job", (q) => q.eq("jobId", args.jobId))
      .collect();
  },
});

export const byJobSelected = query({
  args: { jobId: v.id("jobs") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("videos")
      .withIndex("by_job_selected", (q) =>
        q.eq("jobId", args.jobId).eq("selected", true)
      )
      .collect();
  },
});
