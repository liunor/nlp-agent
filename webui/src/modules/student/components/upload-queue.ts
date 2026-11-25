// A composer owns its queue. Cancelled waiting jobs never issue HTTP requests.
export class UploadQueue {
  private active = 0;
  private pending: Array<() => void> = [];
  constructor(private readonly concurrency = 2) {}

  run<T>(job: () => Promise<T>, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      let started = false;
      const abort = () => {
        if (!started) {
          this.pending = this.pending.filter(entry => entry !== start);
          signal.removeEventListener("abort", abort);
          reject(new DOMException("上传已取消", "AbortError"));
        }
      };
      const start = () => {
        if (signal.aborted) { abort(); return; }
        started = true;
        this.active++;
        let work: Promise<T>;
        try { work = Promise.resolve(job()); }
        catch (error) { work = Promise.reject(error); }
        void work.then(resolve, reject).finally(() => {
          signal.removeEventListener("abort", abort);
          this.active--;
          this.drain();
        });
      };
      signal.addEventListener("abort", abort, { once: true });
      this.pending.push(start);
      if (signal.aborted) abort();
      this.drain();
    });
  }

  private drain() {
    while (this.active < this.concurrency && this.pending.length) this.pending.shift()!();
  }
}
