import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

export const create = mutation({
  args: {
    query: v.string(),
    platforms: v.array(v.string()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("searches", {
      query: args.query,
      status: "classifying",
      platforms: args.platforms,
      outputFormat: "direct",
      resultCount: 0,
      createdAt: Date.now(),
    });
  },
});

export const updateStatus = mutation({
  args: {
    id: v.id("searches"),
    status: v.union(
      v.literal("classifying"),
      v.literal("searching"),
      v.literal("analyzing"),
      v.literal("complete"),
      v.literal("error")
    ),
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

export const get = query({
  args: { id: v.id("searches") },
  handler: async (ctx, args) => {
    return await ctx.db.get(args.id);
  },
});
