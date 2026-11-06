# Convergence provenance and licensing

Brian Baker identified [byrn-baker/Convergence](https://github.com/byrn-baker/Convergence)
as the original standalone project on 30 October 2026. Its [reviewed source](https://github.com/byrn-baker/Convergence/tree/fff81b52fdd9b792e375208abfa8d091adf9949a)
contains the OpenTelemetry, VictoriaMetrics, Grafana, Loki and Alertmanager stack,
Nautobot discovery and network/security dashboards. It is a source reference for
the NetClaw add-on; use the [NetClaw installation guide](../CONVERGENCE.md) for the
current packaging and verified setup commands.

The NetClaw API separately identifies `network-guardian-web` as its predecessor
in its package manifest. Brian confirmed authorship of that code. The reviewed
standalone Convergence tree contains no copy of that API, so it does not establish
the API's file-level history. See the [API notice](../../ui/convergence-api/NOTICE.md)
and [API license](../../ui/convergence-api/LICENSE).

The original standalone repository's MIT notice is reproduced below unchanged.
Retain it with copied or substantially derived source. This attribution does not
relicense NetClaw, its dependencies or third-party charts and dashboards. A focused
upstream port must review the origin and applicable notices of each imported asset.

## Original standalone project license

Source: [LICENSE](https://github.com/byrn-baker/Convergence/blob/fff81b52fdd9b792e375208abfa8d091adf9949a/LICENSE).

```text
MIT License

Copyright (c) 2026 Convergence Project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
