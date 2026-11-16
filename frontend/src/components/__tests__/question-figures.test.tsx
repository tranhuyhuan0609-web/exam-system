// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuestionFigures } from "@/components/exam-room/QuestionFigures";
import { extractFigures, mapPaperPayloadToExamAttempt } from "@/components/exam-room/paper-adapter";

const apiFetch = vi.fn();
vi.mock("@/lib/auth-client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

afterEach(() => {
  cleanup();
  apiFetch.mockReset();
});

const ASSET = "0192a8f0-0000-7000-8000-000000000001";

describe("extractFigures", () => {
  it("reads image entries of stem_json.media and names unnamed ones", () => {
    expect(
      extractFigures({
        text: "x",
        media: [
          { type: "image", asset_id: ASSET, alt: "Hình 1" },
          { type: "image", asset_id: "b" },
          { type: "audio", asset_id: "c" },
          { type: "image" },
          null,
        ],
      }),
    ).toEqual([
      { assetId: ASSET, alt: "Hình 1" },
      { assetId: "b", alt: "Hình 2" },
    ]);
  });

  it("is empty for anything that is not a stem with media", () => {
    expect(extractFigures(undefined)).toEqual([]);
    expect(extractFigures("text")).toEqual([]);
    expect(extractFigures({ media: "nope" })).toEqual([]);
  });
});

describe("paper adapter figures", () => {
  it("puts the figures of a stem on the question, and only there", () => {
    const attempt = mapPaperPayloadToExamAttempt(
      {
        questions: [
          {
            question_version_id: "q1",
            display_position: 1,
            section_code: "PART_1",
            stem_json: { text: "Cho mạch điện", media: [{ type: "image", asset_id: ASSET, alt: "Hình 1" }] },
            options: [{ display_label: "A", content_json: { text: "1" } }],
          },
          { question_version_id: "q2", display_position: 2, section_code: "PART_3", stem_json: { text: "Tính" } },
        ],
      },
      "attempt-1",
    );
    expect(attempt.questions[0]?.figures).toEqual([{ assetId: ASSET, alt: "Hình 1" }]);
    expect(attempt.questions[1]).not.toHaveProperty("figures");
  });
});

describe("QuestionFigures", () => {
  it("fetches the signed URL of each asset and shows the picture", async () => {
    apiFetch.mockResolvedValue({ json: async () => ({ signed_url: `/api/v1/assets/${ASSET}/download?sig=abc` }) });
    render(<QuestionFigures figures={[{ assetId: ASSET, alt: "Hình 1" }]} />);
    expect(screen.getByRole("status")).toBeTruthy();
    const image = (await screen.findByAltText("Hình 1")) as HTMLImageElement;
    expect(image.getAttribute("src")).toBe(`/api/v1/assets/${ASSET}/download?sig=abc`);
    expect(apiFetch).toHaveBeenCalledWith(`/api/v1/assets/${ASSET}/signed-url`, { redirectOn401: false });
  });

  it("says so when the picture cannot be loaded, without breaking the question", async () => {
    apiFetch.mockRejectedValue(new Error("boom"));
    render(<QuestionFigures figures={[{ assetId: ASSET, alt: "Hình 1" }]} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("hình 1"));
  });

  it("renders nothing without figures", () => {
    const { container } = render(<QuestionFigures figures={[]} />);
    expect(container.innerHTML).toBe("");
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
