# Devmark

Devmark is a benchmark for real world development tasks with the goal of comparing machines for that exact use case.

The benchmark measures:

- Git clone
- pnpm install from a local cache
- Vite build of the TanStack Start dashboard
- Cargo build of Bevy's breakout example
- Go build of Podman's remote client

## Run the benchmark

Download and extract this repository, then run:

```sh
./run.sh
```

On Windows, run `run.bat` instead. Install either uv or Python 3.11.8 or newer. With uv, the launcher downloads Python when needed.

Devmark downloads pinned portable tools before timing any workload. You do not need Git, Node.js, pnpm, Rust, Go, or a C compiler installed.
Supported platforms are Windows x86-64, Linux x86-64, Linux arm64, and macOS arm64. Linux requires glibc 2.28 or newer.

The pinned versions are Git 2.51.1, Node.js 22.13.0, pnpm 10.11.0, Rust 1.98.0, and Go 1.26.3.
For Bevy, Devmark also downloads a C compiler and native libraries. On macOS, it downloads Clang 19.1.7 and the macOS 14.5 SDK. On Linux, it downloads GCC 14.2.0. On Windows, it uses GCC 15.2.0 and Rust's GNU target.

Portable installations live in each run's workspace and are removed when the run finishes, including after a failure or interruption.
Downloads and dependency caches stay in `.devmark` for later runs. Preparation requires internet access to resolve packages and fetch missing dependencies.
Devmark saves the resolved toolchain package versions in `.devmark/runs/<run>/locks/toolchains.json`.

To select workloads, run `./run.sh --only vite-build go-build`. Devmark downloads only the toolchains required by the selected workloads.
Use `--list` to list workloads without downloading tools. Use `--keep-workspace` to retain source checkouts and build outputs for inspection.

## Benchmark scores

![File system and CPU benchmark scores](https://tsukinoko-kun.github.io/devmark/overview.svg)

File system scores combine the three Git clone tasks and pnpm install, grouped by kernel name and version.
These scores reflect the tested systems, including their storage hardware.
CPU scores combine the Vite, Cargo, and Go builds, grouped by CPU name and core count.
Each section weights its shared workloads equally using a geometric mean of relative speeds, with its fastest group scoring 100.
Repeated measurements use the median time for each workload within a group.

Regenerate the chart from saved measurements with `python -m devmark.overview`.
