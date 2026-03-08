import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const create = mutation({
  args: {
    username: v.string(),
    platform: v.string(),
    maxVideos: v.number(),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("jobs", {
      username: args.username,
      platform: args.platform,
      status: "scraping",
      maxVideos: args.maxVideos,
      videosProcessed: 0,
      createdAt: Date.now(),
    });
  },
});

export const updateStatus = mutation({
  args: {
    id: v.id("jobs"),
    status: v.string(),
    errorMessage: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const patch: Record<string, unknown> = { status: args.status };
    if (args.errorMessage !== undefined) {
      patch.errorMessage = args.errorMessage;
    }
    await ctx.db.patch(args.id, patch);
  },
});

export const updateProfile = mutation({
  args: {
    id: v.id("jobs"),
    profileDataJson: v.string(),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(args.id, { profileDataJson: args.profileDataJson });
  },
});

export const updateVideosProcessed = mutation({
  args: {
    id: v.id("jobs"),
    videosProcessed: v.number(),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(args.id, { videosProcessed: args.videosProcessed });
  },
});

export const get = query({
  args: { id: v.id("jobs") },
  handler: async (ctx, args) => {
    return await ctx.db.get(args.id);
  },
});
