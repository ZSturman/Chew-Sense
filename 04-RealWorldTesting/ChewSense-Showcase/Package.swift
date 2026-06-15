// swift-tools-version: 5.9
import PackageDescription

let embeddedInfoPlist = "\(Context.packageDirectory)/ChewSenseShowcase-Info.plist"

let package = Package(
    name: "ChewSenseShowcase",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "ChewSenseShowcase",
            path: "Sources/ChewSenseShowcase",
            resources: [
                .copy("Resources/ChewNet.mlpackage"),
                .copy("Resources/chewnet_norm.json"),
                .process("Resources/Samples")
            ],
            linkerSettings: [
                .linkedFramework("CoreMotion"),
                .linkedFramework("CoreML"),
                .linkedFramework("Accelerate"),
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", embeddedInfoPlist
                ], .when(platforms: [.macOS]))
            ]
        ),
        .testTarget(
            name: "ChewSenseShowcaseTests",
            dependencies: ["ChewSenseShowcase"],
            path: "Tests/ChewSenseShowcaseTests"
        )
    ]
)
