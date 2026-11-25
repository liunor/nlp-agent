import { expect, it, vi } from "vitest";
import { UploadQueue } from "./upload-queue";

it("bounds concurrency and removes cancelled waiting uploads", async () => {
  const queue = new UploadQueue(1);
  let release!: () => void;
  const first = queue.run(() => new Promise<void>(resolve => { release = resolve; }), new AbortController().signal);
  const controller = new AbortController();
  const job = vi.fn(async () => "should not run");
  const second = queue.run(job, controller.signal);
  const failure = expect(second).rejects.toMatchObject({ name: "AbortError" });
  controller.abort();
  await failure;
  release();
  await first;
  expect(job).not.toHaveBeenCalled();
});

it("releases the queue when preparing an upload throws synchronously", async () => {
  const queue = new UploadQueue(1);
  const first = queue.run(() => { throw new Error("invalid upload"); }, new AbortController().signal);
  const second = queue.run(async () => "next upload", new AbortController().signal);
  await expect(first).rejects.toThrow("invalid upload");
  await expect(second).resolves.toBe("next upload");
});
