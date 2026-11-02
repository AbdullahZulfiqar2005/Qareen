# How Qareen works

See the maintained [architecture document](docs/ARCHITECTURE.md) and [README](README.md).

The desktop collector records selected observations into SQLite. A separate worker enriches them with local embeddings. Queries retrieve relevant evidence; cloud generation is separately opt-in. Durable jobs create proposals. A human reviews an exact revision before copying the proposal or exporting it as a Gmail draft. Qareen has no message-send or arbitrary-shell execution capability.
