"""Rules added to the page-reading prompt (app/ai/provider.py EXTRACT_PROMPT): exam questions only, the answers and
marking schemes printed in the document attached to their question, no setters' names. They take precedence over
earlier lines of the prompt."""

EXTRACT_RULES = """

QUY TẮC CHỌN LỌC NỘI DUNG (ưu tiên hơn mọi hướng dẫn phía trên):
- CHỈ trích xuất CÂU HỎI CỦA ĐỀ THI. Phần ĐÁP ÁN, BẢNG ĐÁP ÁN, HƯỚNG DẪN CHẤM, LỜI GIẢI, BIỂU ĐIỂM, THANG ĐIỂM
  KHÔNG BAO GIỜ là câu hỏi: không tạo mục nào trong questions từ các phần đó.
- Đáp án / hướng dẫn chấm in trong tài liệu phải GẮN VÀO ĐÚNG CÂU của nó: trắc nghiệm ghi chữ cái vào key_label;
  tự luận, trả lời ngắn, đúng sai ghi nội dung đáp án / hướng dẫn chấm của câu đó vào key (bỏ cột điểm, bỏ số ý thừa).
  Trang chỉ có đáp án (không có câu hỏi nào): trả về questions rỗng.
- KHÔNG chép: tên trường, sở, người ra đề / phản biện / giáo viên, số điện thoại, email, "Họ và tên thí sinh",
  "Số báo danh", "Cán bộ coi thi…", "--- HẾT ---", số trang, mã đề in ở đầu trang.
- Bỏ nhãn ma trận của từng ý như "(NB 0,5 – VDC 1,5)", "(TH 1,0 -- VD 1,0)": chúng không phải nội dung câu hỏi.
- Bảng (bảng số liệu, bảng biến thiên) KHÔNG viết bằng \\begin{tabular}: mỗi hàng một dòng, các ô ngăn bởi " | "
  (VD: "$x$ | $-\\infty$ | $1$ | $+\\infty$"). Nếu bảng nằm trong hình đã khoanh bằng figure_bbox thì không chép lại.
"""
