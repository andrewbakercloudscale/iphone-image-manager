import SwiftUI

/// A disclosure row you can see: a filled chevron badge that turns, and the whole row is the click target.
/// The system style draws a faint grey chevron that is easy to miss.
struct ExpanderStyle: DisclosureGroupStyle {
    var tint: Color = .accentColor

    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { configuration.isExpanded.toggle() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white)
                        .rotationEffect(.degrees(configuration.isExpanded ? 90 : 0))
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(tint))
                    configuration.label
                    Text(configuration.isExpanded ? "Hide" : "Show")
                        .font(.caption.weight(.semibold)).foregroundStyle(tint)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if configuration.isExpanded {
                configuration.content
                    .padding(.leading, 28)
                    .transition(.opacity)
            }
        }
    }
}

extension DisclosureGroupStyle where Self == ExpanderStyle {
    static var expander: ExpanderStyle { ExpanderStyle() }
}
