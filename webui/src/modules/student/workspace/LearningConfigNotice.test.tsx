import { fireEvent, render, screen } from "@testing-library/react";

import { LearningConfigNotice } from "./LearningConfigNotice";

describe("LearningConfigNotice", () => {
  it("does not offer a teacher navigation action for a generic turn error", () => {
    render(<LearningConfigNotice
      requestError={{ code: "turn_conflict", message: "3cee3f82-03fd-4c58-b523-370d21c175c4" }}
      modeNotice={null}
      canManageTeaching={false}
      onNavigate={vi.fn()}
      onClose={vi.fn()}
    />);

    expect(screen.getByRole("alert")).toHaveTextContent("请求未完成");
    expect(screen.getByRole("alert")).toHaveTextContent("上一条请求仍在处理中");
    expect(screen.getByRole("alert")).not.toHaveTextContent("3cee3f82-03fd-4c58-b523-370d21c175c4");
    expect(screen.queryByRole("button", { name: "去配置" })).not.toBeInTheDocument();
  });

  it("only allows a teacher to open the matching configuration page", () => {
    const onNavigate = vi.fn();
    render(<LearningConfigNotice
      requestError={null}
      modeNotice="review"
      canManageTeaching
      onNavigate={onNavigate}
      onClose={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "去配置" }));
    expect(onNavigate).toHaveBeenCalledWith("/teacher/reviews");
  });
});
