import { TableClient } from "./table-client";

export default async function TablePage({ params }: { params: Promise<{ tableId: string }> }) {
  const { tableId } = await params;
  return <TableClient tableId={Number(tableId)} />;
}
