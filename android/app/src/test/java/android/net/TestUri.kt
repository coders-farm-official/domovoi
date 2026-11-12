package android.net

import android.os.Parcel

/**
 * A Uri the JVM tests can hold. The unit tests' android.jar answers null
 * from Uri.parse, so a media item's artwork would always read as "none";
 * this one keeps the address it was made from (SessionMedia.uriOf).
 */
class TestUri(private val address: String) : Uri() {
    override fun toString(): String = address
    override fun equals(other: Any?): Boolean = other is TestUri && other.address == address
    override fun hashCode(): Int = address.hashCode()
    override fun buildUpon(): Uri.Builder = throw UnsupportedOperationException()
    override fun getAuthority(): String? = null
    override fun getEncodedAuthority(): String? = null
    override fun getEncodedFragment(): String? = null
    override fun getEncodedPath(): String? = null
    override fun getEncodedQuery(): String? = null
    override fun getEncodedSchemeSpecificPart(): String? = null
    override fun getEncodedUserInfo(): String? = null
    override fun getFragment(): String? = null
    override fun getHost(): String? = null
    override fun getLastPathSegment(): String? = null
    override fun getPath(): String? = null
    override fun getPathSegments(): List<String> = emptyList()
    override fun getPort(): Int = -1
    override fun getQuery(): String? = null
    override fun getScheme(): String? = address.substringBefore(':', "").ifEmpty { null }
    override fun getSchemeSpecificPart(): String? = null
    override fun getUserInfo(): String? = null
    override fun isHierarchical(): Boolean = true
    override fun isRelative(): Boolean = getScheme() == null
    override fun describeContents(): Int = 0
    override fun writeToParcel(dest: Parcel, flags: Int) = throw UnsupportedOperationException()
}
