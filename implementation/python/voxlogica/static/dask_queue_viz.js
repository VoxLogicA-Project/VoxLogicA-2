(() => {
  const LANE_ORDER = ["queued", "running", "completed", "failed"];
  const LANE_COLORS = {
    queued: "rgba(120, 154, 255, 0.95)",
    running: "rgba(71, 236, 179, 0.95)",
    completed: "rgba(166, 255, 187, 0.95)",
    failed: "rgba(255, 136, 156, 0.95)",
  };

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const toInt = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num)) return 0;
    return Math.max(0, Math.floor(num));
  };

  class DaskQueueVisualizer {
    constructor(root) {
      this.root = root;
      this.snapshot = {
        counts: { queued: 0, running: 0, completed: 0, failed: 0, total: 0 },
        jobs: [],
        node_events: [],
      };
      this.canvas = null;
      this.ctx = null;
      this.metricsEl = null;
      this.jobsEl = null;
      this.particles = [];
      this.laneTargets = { queued: 0, running: 0, completed: 0, failed: 0 };
      this.lastFrame = performance.now();
      this.raf = null;
      this._resizeBound = () => this._resizeCanvas();
      this._build();
      window.addEventListener("resize", this._resizeBound);
      this._tick();
    }

    destroy() {
      if (this.raf) cancelAnimationFrame(this.raf);
      window.removeEventListener("resize", this._resizeBound);
    }

    render(snapshot) {
      const counts = (snapshot && snapshot.counts) || {};
      const jobs = Array.isArray(snapshot && snapshot.jobs) ? snapshot.jobs : [];
      const nodeEvents = Array.isArray(snapshot && snapshot.node_events) ? snapshot.node_events : [];
      this.snapshot = {
        generated_at: snapshot && snapshot.generated_at ? snapshot.generated_at : Date.now(),
        active_job_id: snapshot && snapshot.active_job_id ? String(snapshot.active_job_id) : "",
        counts: {
          queued: toInt(counts.queued),
          running: toInt(counts.running),
          completed: toInt(counts.completed),
          failed: toInt(counts.failed + counts.killed),
          total: toInt(counts.total),
        },
        jobs: jobs.slice(0, 20),
        node_events: nodeEvents.slice(-260),
      };
      this._updateMetrics();
      this._updateJobs();
      this._retargetParticles();
    }

    _build() {
      this.root.innerHTML = `
        <section class="dqv-shell">
          <div class="dqv-metrics"></div>
          <div class="dqv-canvas-wrap">
            <div class="dqv-lanes">
              <span>Queued</span>
              <span>Running</span>
              <span>Completed</span>
              <span>Failed</span>
            </div>
            <canvas class="dqv-canvas" height="180"></canvas>
          </div>
          <div class="dqv-jobs"></div>
        </section>
      `;
      this.metricsEl = this.root.querySelector(".dqv-metrics");
      this.jobsEl = this.root.querySelector(".dqv-jobs");
      this.canvas = this.root.querySelector(".dqv-canvas");
      this.ctx = this.canvas ? this.canvas.getContext("2d") : null;
      this._resizeCanvas();
      this._updateMetrics();
      this._updateJobs();
      this._retargetParticles();
    }

    _resizeCanvas() {
      if (!this.canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = this.canvas.getBoundingClientRect();
      const width = Math.max(220, Math.floor(rect.width || this.root.clientWidth || 360));
      const height = Math.max(160, Math.floor(rect.height || 180));
      this.canvas.width = Math.floor(width * dpr);
      this.canvas.height = Math.floor(height * dpr);
      if (this.ctx) this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    _updateMetrics() {
      if (!this.metricsEl) return;
      const counts = this.snapshot.counts || {};
      const cards = [
        ["Queued", counts.queued],
        ["Running", counts.running],
        ["Completed", counts.completed],
        ["Failed", counts.failed],
        ["Total", counts.total],
      ];
      this.metricsEl.innerHTML = cards
        .map(
          ([label, value]) => `
            <div class="dqv-pill">
              <span>${String(label).toUpperCase()}</span>
              <b>${toInt(value)}</b>
            </div>
          `,
        )
        .join("");
    }

    _updateJobs() {
      if (!this.jobsEl) return;
      const jobs = this.snapshot.jobs || [];
      if (!jobs.length) {
        this.jobsEl.innerHTML = `<div class="muted">No queue jobs yet.</div>`;
        return;
      }
      const active = this.snapshot.active_job_id || "";
      this.jobsEl.innerHTML = jobs
        .slice(0, 12)
        .map((job) => {
          const status = String(job.status || "unknown").toLowerCase();
          const id = String(job.id || "").slice(0, 12);
          const kind = String(job.kind || "run");
          const strategy = String(job.strategy || "dask");
          const activeBadge = active && String(job.id || "") === active ? " | active" : "";
          return `
            <article class="dqv-job">
              <div>
                <div class="dqv-job-id">${id}</div>
                <div class="dqv-job-kind">${kind} | ${strategy}${activeBadge}</div>
              </div>
              <span class="dqv-job-status ${status}">${status}</span>
              <span class="muted">${job.wall_time_s ? `${Number(job.wall_time_s).toFixed(2)}s` : "-"}</span>
            </article>
          `;
        })
        .join("");
    }

    _laneDesiredCount(lane, counts) {
      const base = toInt(counts[lane]);
      if (base <= 0) return lane === "running" ? 4 : 2;
      return clamp(Math.round(4 + Math.sqrt(base) * 5), 4, 46);
    }

    _retargetParticles() {
      const counts = this.snapshot.counts || {};
      for (const lane of LANE_ORDER) {
        this.laneTargets[lane] = this._laneDesiredCount(lane, counts);
      }
      for (const lane of LANE_ORDER) {
        const current = this.particles.filter((p) => p.lane === lane);
        const target = this.laneTargets[lane];
        if (current.length < target) {
          const needed = target - current.length;
          for (let i = 0; i < needed; i += 1) {
            this.particles.push(this._newParticle(lane));
          }
        } else if (current.length > target) {
          let toDrop = current.length - target;
          this.particles = this.particles.filter((p) => {
            if (toDrop > 0 && p.lane === lane) {
              toDrop -= 1;
              return false;
            }
            return true;
          });
        }
      }
    }

    _laneY(lane, height) {
      const idx = LANE_ORDER.indexOf(lane);
      const top = 36;
      const usable = Math.max(40, height - top - 18);
      return top + ((idx + 0.5) / LANE_ORDER.length) * usable;
    }

    _newParticle(lane) {
      const width = this.canvas ? this.canvas.clientWidth : 640;
      return {
        lane,
        x: Math.random() * width,
        phase: Math.random() * Math.PI * 2,
        speed: 28 + Math.random() * 64,
        radius: 1.2 + Math.random() * 2.6,
      };
    }

    _drawBackground(ctx, width, height) {
      const grad = ctx.createLinearGradient(0, 0, width, height);
      grad.addColorStop(0, "rgba(12, 32, 50, 0.95)");
      grad.addColorStop(1, "rgba(6, 13, 23, 0.95)");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
      ctx.lineWidth = 1;
      const grid = 28;
      for (let x = 0; x < width; x += grid) {
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, height);
        ctx.stroke();
      }
      for (let y = 0; y < height; y += grid) {
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(width, y + 0.5);
        ctx.stroke();
      }
    }

    _drawLanes(ctx, width, height) {
      ctx.lineWidth = 1.2;
      for (const lane of LANE_ORDER) {
        const y = this._laneY(lane, height);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }
    }

    _drawEvents(ctx, width, height) {
      const events = this.snapshot.node_events || [];
      if (!events.length) return;
      const startX = width * 0.12;
      const endX = width * 0.96;
      const span = Math.max(1, events.length - 1);
      for (let i = 0; i < events.length; i += 1) {
        const event = events[i];
        const lane = LANE_ORDER.includes(event.status) ? event.status : "running";
        const y = this._laneY(lane, height);
        const x = startX + ((endX - startX) * i) / span;
        const alpha = 0.18 + (0.62 * i) / events.length;
        ctx.fillStyle = (LANE_COLORS[lane] || "rgba(255,255,255,0.8)").replace("0.95", alpha.toFixed(2));
        ctx.beginPath();
        ctx.arc(x, y, 1.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    _drawParticles(ctx, width, height, dt) {
      for (const particle of this.particles) {
        const laneY = this._laneY(particle.lane, height);
        particle.x += (particle.speed * dt) / 1000;
        particle.phase += dt * 0.004;
        if (particle.x > width + 8) {
          particle.x = -8;
          particle.phase = Math.random() * Math.PI * 2;
        }
        const y = laneY + Math.sin(particle.phase) * 7;
        ctx.fillStyle = LANE_COLORS[particle.lane] || "rgba(255, 255, 255, 0.9)";
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(particle.x, y, particle.radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;
    }

    _tick() {
      const now = performance.now();
      const dt = clamp(now - this.lastFrame, 8, 64);
      this.lastFrame = now;
      if (this.ctx && this.canvas) {
        const width = this.canvas.clientWidth || 1;
        const height = this.canvas.clientHeight || 1;
        this._drawBackground(this.ctx, width, height);
        this._drawLanes(this.ctx, width, height);
        this._drawEvents(this.ctx, width, height);
        this._drawParticles(this.ctx, width, height, dt);
      }
      this.raf = requestAnimationFrame(() => this._tick());
    }
  }

  window.VoxDaskQueueViz = {
    DaskQueueVisualizer,
  };
})();
