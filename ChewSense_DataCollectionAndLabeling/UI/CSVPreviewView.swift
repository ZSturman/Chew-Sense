//import SwiftUI
//
//struct CSVPreviewView: View {
//    private let csvText: String
//    private let maxCharacters = 5000
//
//    init() {
//        if let url = Bundle.main.url(forResource: "data", withExtension: "csv"),
//           let content = try? String(contentsOf: url) {
//            if content.count > maxCharacters {
//                let index = content.index(content.startIndex, offsetBy: maxCharacters)
//                csvText = String(content[..<index]) + "\n\n... (truncated)"
//            } else {
//                csvText = content
//            }
//        } else {
//            csvText = "Unable to load CSV file."
//        }
//    }
//
//    var body: some View {
//        ScrollView {
//            Text(csvText)
//                .font(.system(.body, design: .monospaced))
//                .padding()
//                .frame(maxWidth: .infinity, alignment: .leading)
//        }
//    }
//}
//
//struct CSVPreviewView_Previews: PreviewProvider {
//    static var previews: some View {
//        CSVPreviewView()
//    }
//}
