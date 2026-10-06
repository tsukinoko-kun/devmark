# Devmark

Devmark is a benchmark for real world development tasks with the goal of comparing machines for that exact use case.

The benchmark measures:

- Git clone
- pnpm install from a local cache
- Vite build of the TanStack Start dashboard
- Cargo build of Bevy's breakout example
- Go build of Podman's remote client

![File system and CPU benchmark scores](https://tsukinoko-kun.github.io/devmark/overview.svg)

File system scores combine the three Git clone tasks and pnpm install, grouped by kernel name and version.
These scores reflect the tested systems, including their storage hardware.
CPU scores combine the Vite, Cargo, and Go builds, grouped by CPU name and core count.
Each section weights its shared workloads equally using a geometric mean of relative speeds, with its fastest group scoring 100.
Repeated measurements use the median time for each workload within a group.

Regenerate the chart from saved measurements with `python -m devmark.overview`.
