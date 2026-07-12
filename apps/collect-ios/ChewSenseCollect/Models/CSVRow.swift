//
//  CSVRow.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//


import Foundation

    struct CSVRow: Identifiable {
        let id = UUID()
        let timestamp: Double
        var fieldsWithoutLabel: [String]
        var label: Bool?
    }

    struct LabelMarker: Identifiable {
        let id = UUID()
        var time: Double
        var labelForNext: Bool
    }
