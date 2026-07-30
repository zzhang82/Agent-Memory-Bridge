import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";

const [serverPython, projectRoot, runtimeDir] = process.argv.slice(2);
if (!serverPython || !projectRoot || !runtimeDir) {
  throw new Error("usage: node scripts/mcp_ts_compat.mjs <server-python> <project-root> <runtime-dir>");
}

const boundarySource = fs.readFileSync(
  path.join(path.resolve(projectRoot), "src", "agent_mem_bridge", "mcp_boundary.py"),
  "utf8",
);
const orderBlock = boundarySource.match(/PUBLIC_TOOL_ORDER = \(([\s\S]*?)\n\)/);
assert.ok(orderBlock, "PUBLIC_TOOL_ORDER must exist in mcp_boundary.py");
const expectedTools = [...orderBlock[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
assert.equal(expectedTools.length, 13);

const resolvedRuntime = path.resolve(runtimeDir);
const serverCommand =
  path.isAbsolute(serverPython) || serverPython.includes("/") || serverPython.includes("\\")
    ? path.resolve(serverPython)
    : serverPython;
const client = new Client(
  { name: "amb-typescript-sdk-v2-proof", version: "2.0.0" },
  { versionNegotiation: { mode: "auto" } },
);
const transport = new StdioClientTransport({
  command: serverCommand,
  args: ["-m", "agent_mem_bridge"],
  cwd: path.resolve(projectRoot),
  env: {
    ...process.env,
    AGENT_MEMORY_BRIDGE_HOME: resolvedRuntime,
    AGENT_MEMORY_BRIDGE_DB_PATH: path.join(resolvedRuntime, "typescript-compat.db"),
    AGENT_MEMORY_BRIDGE_LOG_DIR: path.join(resolvedRuntime, "logs"),
  },
  stderr: "pipe",
});

try {
  await client.connect(transport);
  assert.equal(client.getProtocolEra(), "modern");
  assert.equal(client.getNegotiatedProtocolVersion(), "2026-07-28");

  const discover = client.getDiscoverResult();
  assert.ok(discover);
  assert.deepEqual(discover.supportedVersions, ["2026-07-28"]);
  assert.equal(discover.resultType, "complete");
  assert.equal(discover.ttlMs, 300000);
  assert.equal(discover.cacheScope, "public");

  const listed = await client.listTools(undefined, { cacheMode: "bypass" });
  assert.deepEqual(listed.tools.map((tool) => tool.name), expectedTools);

  const stored = await client.callTool({
    name: "store",
    arguments: {
      namespace: "project:typescript-sdk-v2-proof",
      content: "TypeScript MCP SDK 2.0.0 modern interoperability proof.",
      kind: "memory",
    },
  });
  assert.equal(stored.isError, false);
  assert.equal(stored.structuredContent.stored, true);

  const recalled = await client.callTool({
    name: "recall",
    arguments: {
      namespace: "project:typescript-sdk-v2-proof",
      query: "modern interoperability proof",
      kind: "memory",
      limit: 5,
    },
  });
  assert.equal(recalled.isError, false);
  assert.ok(recalled.structuredContent.count >= 1);

  console.log(
    JSON.stringify(
      {
        ok: true,
        client: "@modelcontextprotocol/client@2.0.0",
        protocol_version: client.getNegotiatedProtocolVersion(),
        protocol_era: client.getProtocolEra(),
        tool_count: listed.tools.length,
        discover_cache: { ttlMs: discover.ttlMs, cacheScope: discover.cacheScope },
      },
      null,
      2,
    ),
  );
} finally {
  await client.close();
}
