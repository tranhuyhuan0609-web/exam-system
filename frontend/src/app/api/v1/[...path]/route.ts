import { proxyApiV1Request } from "@/lib/backend-proxy";

export const dynamic = "force-dynamic";

async function handle(request: Request): Promise<Response> {
  return proxyApiV1Request(request);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
export const HEAD = handle;
export const OPTIONS = handle;
