import type { UpdateParameters } from '@deck.gl/core';
import { H3HexagonLayer } from '@deck.gl/geo-layers';

/** Fixed tile centers make the instanced H3 mesh independent of the camera.
 * Deck still updates projection uniforms when drawing. Avoid rebuilding the
 * composite ColumnLayer for every hidden resident tile on every zoom frame.
 */
export class StableH3HexagonLayer<DataT> extends H3HexagonLayer<DataT> {
  static layerName = 'StableH3HexagonLayer';

  override shouldUpdateState(parameters: UpdateParameters<this>): boolean {
    const { changeFlags } = parameters;
    if (
      this.props.highPrecision === false &&
      this.props.centerHexagon &&
      changeFlags.viewportChanged &&
      !changeFlags.propsOrDataChanged &&
      !changeFlags.stateChanged
    ) {
      return false;
    }
    return super.shouldUpdateState(parameters);
  }
}
